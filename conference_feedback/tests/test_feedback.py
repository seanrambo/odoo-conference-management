from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged


class FeedbackCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.now = fields.Datetime.now().replace(microsecond=0)
        cls.event = cls.env['event.event'].create({
            'name': 'East Africa Health Summit',
            'date_begin': cls.now - timedelta(days=1),
            'date_end': cls.now + timedelta(hours=6),
            'date_tz': 'Africa/Nairobi',
            'cpd_enabled': True,
            'cpd_label': 'CPD points',
        })
        cls.speaker = cls.env['res.partner'].create({'name': 'Dr Achieng Otieno', 'email': 'achieng@example.com'})
        cls.speaker_2 = cls.env['res.partner'].create({'name': 'Prof Kamau Njoroge', 'email': 'kamau@example.com'})
        cls.hall = cls.env['event.track.location'].create({'name': 'Hall A'})
        cls.stage_cancel = cls.env['event.track.stage'].create({'name': 'Cancelled', 'is_cancel': True})
        # Ended 3 hours ago, ended 20 minutes ago, and starting in 2 hours.
        cls.track_done = cls._track(cls, 'Opening keynote', cls.now - timedelta(hours=4), cpd=1.5)
        cls.track_just_done = cls._track(cls, 'Malaria update', cls.now - timedelta(minutes=80), cpd=2.0,
                                         speaker=cls.speaker_2)
        cls.track_future = cls._track(cls, 'Closing panel', cls.now + timedelta(hours=2), cpd=1.0)
        cls.reg = cls._register(cls, 'Wanjiku Mwangi', 'wanjiku@example.com')
        cls.reg_2 = cls._register(cls, 'Otieno Ouma', 'otieno@example.com')
        cls.Feedback = cls.env['conference.session.feedback']

    def _track(self, name, date, cpd=0.0, speaker=None):
        return self.env['event.track'].create({
            'name': name, 'event_id': self.event.id, 'date': date, 'duration': 1.0,
            'location_id': self.hall.id, 'partner_id': (speaker or self.speaker).id,
            'cpd_points': cpd, 'onsite_seat_limit': 100,
        })

    def _register(self, name, email, state='done'):
        reg = self.env['event.registration'].create({'event_id': self.event.id, 'name': name, 'email': email})
        reg.state = state
        return reg

    def _attend(self, track, reg):
        return self.env['conference.session.attendance'].create({'track_id': track.id, 'registration_id': reg.id})

    def _mails(self, model, res_id=None, email=None):
        domain = [('model', '=', model)]
        if res_id:
            domain.append(('res_id', '=', res_id))
        if email:
            domain.append(('email_to', 'ilike', email))
        return self.env['mail.mail'].sudo().search(domain)

    def _finish_event(self):
        self.event.date_end = self.now - timedelta(minutes=30)


@tagged('post_install', '-at_install')
class TestSessionFeedback(FeedbackCase):

    def test_pending_feedback_respects_delay_cancel_and_lookback(self):
        self.event.feedback_session_delay = 15
        for track in (self.track_done, self.track_just_done, self.track_future):
            self._attend(track, self.reg)
        cancelled = self._track('Dropped workshop', self.now - timedelta(hours=4))
        self._attend(cancelled, self.reg)
        cancelled.with_context(onsite_skip_change_notify=True).stage_id = self.stage_cancel
        old = self._track('Last week', self.now - timedelta(days=5))
        self._attend(old, self.reg)

        created = self.Feedback._create_pending_feedback()
        self.assertEqual(created.track_id, self.track_done | self.track_just_done,
                         "Ended sessions only; not cancelled, not older than the lookback")
        self.assertFalse(self.Feedback._create_pending_feedback(), "Running again creates nothing new")

        self.event.feedback_session_delay = 120
        self.Feedback.search([]).unlink()
        self.assertEqual(self.Feedback._create_pending_feedback().track_id, self.track_done,
                         "A session that ended 20 minutes ago waits for a 2-hour delay")

    def test_disabled_event_asks_nothing(self):
        self.event.feedback_session_enabled = False
        self._attend(self.track_done, self.reg)
        self.assertFalse(self.Feedback._create_pending_feedback())

    def test_requests_are_grouped_and_throttled(self):
        self.event.feedback_email_gap_hours = 4
        self._attend(self.track_done, self.reg)
        self._attend(self.track_just_done, self.reg)
        self._attend(self.track_done, self.reg_2)
        self.Feedback._cron_request_feedback()

        mails = self._mails('event.registration', self.reg.id)
        self.assertEqual(len(mails), 1, "One email for both sessions")
        self.assertIn('Opening keynote', str(mails.body_html))
        self.assertIn('Malaria update', str(mails.body_html))
        self.assertIn(self.reg.sudo().feedback_token, str(mails.body_html))
        self.assertEqual(len(self._mails('event.registration', self.reg_2.id)), 1)
        self.assertTrue(all(self.Feedback.search([]).mapped('request_sent')))
        self.assertNotEqual(self.reg.sudo().feedback_token, self.reg_2.sudo().feedback_token)

        # A later session ends: no new email inside the 4-hour gap, then one email for it alone.
        later = self.Feedback.create({'track_id': self.track_future.id, 'registration_id': self.reg.id})
        self.Feedback._send_feedback_requests(now=self.now + timedelta(hours=1))
        self.assertFalse(later.request_sent)
        self.Feedback._send_feedback_requests(now=self.now + timedelta(hours=5))
        self.assertTrue(later.request_sent)
        mails = self._mails('event.registration', self.reg.id)
        self.assertEqual(len(mails), 2)
        newest = mails.sorted('id')[-1]
        self.assertIn('Closing panel', str(newest.body_html))
        self.assertNotIn('Opening keynote', str(newest.body_html))

    def test_record_answer_and_stats(self):
        fb = self.Feedback.create({'track_id': self.track_done.id, 'registration_id': self.reg.id})
        self.assertFalse(fb._record_answer(0))
        self.assertFalse(fb._record_answer('6'))
        self.assertFalse(fb._record_answer('great'))
        self.assertEqual(fb.state, 'pending')
        self.assertTrue(fb._record_answer('4', '  Clear and practical.  '))
        self.assertEqual((fb.state, fb.rating, fb.comment), ('done', 4, 'Clear and practical.'))
        with self.assertRaises(ValidationError):
            fb.rating = 9

        fb2 = self.Feedback.create({'track_id': self.track_done.id, 'registration_id': self.reg_2.id})
        fb2._record_answer(5)
        self.assertEqual(self.track_done.feedback_count, 2)
        self.assertEqual(self.track_done.feedback_rating_avg, 4.5)
        self.assertEqual(self.event.feedback_count, 2)
        self.assertEqual(self.event.feedback_rating_avg, 4.5)

    def test_speaker_scorecards(self):
        self._attend(self.track_done, self.reg)
        self._attend(self.track_done, self.reg_2)
        for reg, rating, comment in ((self.reg, 5, 'Inspiring.'), (self.reg_2, 4, False)):
            self.Feedback.create({'track_id': self.track_done.id, 'registration_id': reg.id})._record_answer(rating, comment)

        cards = self.event._feedback_speaker_scorecards()
        self.assertEqual(set(cards), {self.speaker, self.speaker_2})
        keynote = next(r for r in cards[self.speaker] if r['name'] == 'Opening keynote')
        self.assertEqual((keynote['attended'], keynote['rating_count'], keynote['rating_avg']), (2, 2, 4.5))
        self.assertEqual(keynote['comments'], ['Inspiring.'])

        self.assertEqual(self.event._feedback_send_speaker_scorecards(), 2)
        mail = self._mails('event.event', self.event.id, 'achieng@example.com')
        self.assertEqual(len(mail), 1)
        self.assertIn('4.5', str(mail.body_html))
        self.assertIn('Inspiring.', str(mail.body_html))
        self.assertNotIn('Wanjiku', str(mail.body_html), "Comments are shared without names")


@tagged('post_install', '-at_install')
class TestSurveyAndCertificates(FeedbackCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.survey = cls.env['survey.survey'].create({
            'title': 'Summit feedback',
            'access_mode': 'token',
            'question_ids': [(0, 0, {'title': 'Would you come again?', 'question_type': 'simple_choice',
                                     'suggested_answer_ids': [(0, 0, {'value': 'Yes'}), (0, 0, {'value': 'No'})]})],
        })
        cls.reg_absent = cls._register(cls, 'Absent Person', 'absent@example.com', state='open')

    def test_survey_goes_to_checked_in_attendees(self):
        self.event.feedback_survey_id = self.survey
        sent = self.event._feedback_send_event_survey()
        self.assertEqual(sent, self.reg | self.reg_2)
        answer = self.reg.feedback_survey_input_id
        self.assertEqual(answer.survey_id, self.survey)
        self.assertEqual(answer.email, 'wanjiku@example.com')
        self.assertEqual(answer.conference_registration_id, self.reg)
        mail = self._mails('event.registration', self.reg.id)
        self.assertIn(answer.access_token, str(mail.body_html))
        self.assertTrue(self.event.feedback_survey_sent_date)

        self.assertFalse(self.event._feedback_send_event_survey(), "Nobody gets a second link")
        self.event.feedback_survey_audience = 'all'
        self.assertEqual(self.event._feedback_send_event_survey(), self.reg_absent)

    def test_survey_for_attendee_booked_by_someone_else(self):
        buyer = self.env['res.partner'].create({'name': 'Acme HR', 'email': 'hr@acme.example.com'})
        self.reg.partner_id = buyer
        self.event.feedback_survey_id = self.survey
        self.event._feedback_send_event_survey()
        answer = self.reg.feedback_survey_input_id
        self.assertEqual(answer.email, 'wanjiku@example.com')
        self.assertFalse(answer.partner_id, "The buyer is not answering for the attendee")

    def test_archived_survey_is_skipped(self):
        self.survey.active = False
        self.event.feedback_survey_id = self.survey
        self.assertFalse(self.event._feedback_send_event_survey())

    def test_certificate_rules(self):
        self.event.write({'certificate_enabled': True, 'certificate_min_sessions': 2, 'certificate_min_cpd': 3.0,
                          'feedback_survey_id': self.survey.id, 'certificate_require_survey': True})
        eligible, reasons = self.reg._certificate_eligibility()
        self.assertFalse(eligible)
        self.assertEqual(len(reasons), 3)
        self.assertFalse(self.reg_absent._certificate_eligibility()[0])
        self.assertIn('not checked in to the event', self.reg_absent._certificate_eligibility()[1])

        self._attend(self.track_done, self.reg)
        self._attend(self.track_just_done, self.reg)
        self.event._feedback_send_event_survey()
        eligible, reasons = self.reg._certificate_eligibility()
        self.assertEqual(reasons, ['has not completed the event survey'])
        self.reg.feedback_survey_input_id._mark_done()
        self.assertTrue(self.reg._certificate_eligibility()[0])

    def test_issue_certificates_from_event(self):
        self.event.write({'certificate_enabled': True, 'certificate_min_sessions': 1})
        self._attend(self.track_done, self.reg)
        self._attend(self.track_just_done, self.reg)
        res = self.event.action_issue_certificates()
        self.assertIn('1 certificates issued', res['params']['message'])
        self.assertIn('2 attendees', res['params']['message'], "reg_2 has no sessions; reg_absent didn't check in")
        cert = self.reg.certificate_id
        self.assertTrue(cert.name.startswith('CERT'))
        self.assertEqual((cert.attendee_name, cert.session_count, cert.cpd_points), ('Wanjiku Mwangi', 2, 3.5))
        self.assertEqual(cert.method, 'rules')

        mail = self._mails('conference.certificate', cert.id)
        self.assertEqual(len(mail), 1)
        self.assertEqual(mail.email_to or mail.recipient_ids.email, 'wanjiku@example.com')
        self.assertTrue(mail.attachment_ids, "The certificate is attached")
        self.assertIn(cert._get_download_url(), str(mail.body_html))

        self.assertFalse(self.event._certificate_issue_eligible()[0], "No second certificate")
        # Points are fixed at issue.
        self.track_done.cpd_points = 10
        self.assertEqual(cert.cpd_points, 3.5)

    def test_email_failure_does_not_block_issuing(self):
        """No PDF engine on the server, say: certificates are still issued, and the failure is logged."""
        self.event.write({'certificate_enabled': True})
        MailTemplate = type(self.env['mail.template'])
        original = MailTemplate.send_mail

        def send_mail(template, res_id, *args, **kwargs):
            if self.env['conference.certificate'].browse(res_id).registration_id == self.reg:
                raise Exception("Unable to find Wkhtmltopdf on this system.")
            return original(template, res_id, *args, **kwargs)

        with patch.object(MailTemplate, 'send_mail', send_mail):
            certificates, _not_eligible = self.event._certificate_issue_eligible()
        self.assertEqual(certificates.registration_id, self.reg | self.reg_2)
        self.assertIn('could not be emailed', self.reg.certificate_id.message_ids[0].body)
        self.assertTrue(self._mails('conference.certificate', self.reg_2.certificate_id.id))

    def test_cpd_not_printed_when_cpd_off(self):
        self.event.write({'certificate_enabled': True, 'cpd_enabled': False})
        self._attend(self.track_done, self.reg)
        cert = self.reg._certificate_issue(send=False)
        self.assertEqual(cert.cpd_points, 0)

    def test_manual_certificate_overrides_rules(self):
        self.event.write({'certificate_enabled': True, 'certificate_min_sessions': 5})
        res = self.reg_absent.action_issue_certificate()
        cert = self.reg_absent.certificate_id
        self.assertEqual(res['res_id'], cert.id)
        self.assertEqual(cert.method, 'manual')

    def test_certificate_report_html(self):
        self.event.write({'certificate_enabled': True, 'certificate_signatory_name': 'Dr Grace Wanjiru'})
        self._attend(self.track_done, self.reg)
        cert = self.reg._certificate_issue(send=False)
        html = self.env['ir.actions.report']._render_qweb_html(
            'conference_feedback.action_report_certificate', cert.ids)[0].decode()
        for text in ('Certificate of Attendance', 'Wanjiku Mwangi', 'East Africa Health Summit', cert.name,
                     'CPD points', 'Dr Grace Wanjiru', cert._get_verify_url(), '/report/barcode/QR/'):
            self.assertIn(text, html)
        self.assertNotIn('REVOKED', html)
        cert.action_revoke()
        html = self.env['ir.actions.report']._render_qweb_html(
            'conference_feedback.action_report_certificate', cert.ids)[0].decode()
        self.assertIn('REVOKED', html)

    def test_survey_completion_issues_certificate_after_event(self):
        self.event.write({'certificate_enabled': True, 'certificate_auto': True,
                          'feedback_survey_id': self.survey.id, 'certificate_require_survey': True})
        self._attend(self.track_done, self.reg)
        self.event._feedback_send_event_survey()
        # During the event: completing the survey does not issue yet.
        self.reg.feedback_survey_input_id._mark_done()
        self.assertFalse(self.reg.certificate_id)
        # After the event: completing it does.
        self._finish_event()
        self.reg_2.feedback_survey_input_id._mark_done()
        self.assertTrue(self.reg_2.certificate_id)

    def test_post_event_cron(self):
        self.event.write({
            'feedback_survey_id': self.survey.id, 'feedback_survey_delay_hours': 2,
            'certificate_enabled': True, 'certificate_auto': True,
        })
        self._attend(self.track_done, self.reg)
        Event = self.env['event.event']
        Event._cron_post_event()
        self.assertFalse(self.reg.feedback_survey_input_id, "Event not over")
        self.assertFalse(self.reg.certificate_id)

        self._finish_event()  # ended 30 minutes ago
        Event._cron_post_event()
        self.assertFalse(self.reg.feedback_survey_input_id, "Survey waits 2 hours")
        self.assertTrue(self.reg.certificate_id, "Certificates go out as soon as the event ends")
        self.assertTrue(self.reg_2.certificate_id, "No minimums set: checked in is enough")
        self.assertFalse(self.reg_absent.certificate_id)

        self.event.date_end = self.now - timedelta(hours=3)
        Event._cron_post_event()
        self.assertTrue(self.reg.feedback_survey_input_id)


@tagged('post_install', '-at_install')
class TestEventReport(FeedbackCase):

    def test_report_data(self):
        self._register('Unconfirmed Person', 'draft@example.com', state='draft')
        self._register('Cancelled Person', 'cancel@example.com', state='cancel')
        self._register('Not Here', 'nothere@example.com', state='open')
        self._attend(self.track_done, self.reg)
        self._attend(self.track_done, self.reg_2)
        self._attend(self.track_just_done, self.reg)
        self.Feedback.create({'track_id': self.track_done.id, 'registration_id': self.reg.id})._record_answer(5)
        self.Feedback.create({'track_id': self.track_done.id, 'registration_id': self.reg_2.id})._record_answer(3)
        self.Feedback.create({'track_id': self.track_just_done.id, 'registration_id': self.reg.id})
        self.track_future.with_context(onsite_skip_change_notify=True).stage_id = self.stage_cancel

        data = self.event.get_conference_report_data()
        k = data['kpis']
        self.assertEqual((k['registered'], k['attended'], k['checkin_rate']), (3, 2, 67))
        self.assertEqual((k['cancelled'], k['unconfirmed']), (1, 1))
        self.assertEqual((k['sessions'], k['session_scans']), (2, 3))
        self.assertEqual((k['rating_avg'], k['rating_count'], k['feedback_rate']), (4.0, 2, 67))
        self.assertEqual(k['cpd_total'], 5.0)

        self.assertEqual(data['timeline'][-1]['total'], 3)
        self.assertEqual([s['name'] for s in data['sessions']], ['Opening keynote', 'Malaria update', 'Closing panel'])
        keynote = data['sessions'][0]
        self.assertEqual((keynote['attended'], keynote['capacity'], keynote['rating_avg'], keynote['rating_count']),
                         (2, 100, 4.0, 2))
        self.assertTrue(data['sessions'][2]['cancelled'])
        self.assertEqual(data['sessions'][1]['rating_avg'], None)
        speakers = {s['name']: s for s in data['speakers']}
        self.assertEqual(speakers['Dr Achieng Otieno']['sessions'], 1, "Cancelled sessions don't count")
        self.assertEqual(speakers['Dr Achieng Otieno']['rating_avg'], 4.0)
        self.assertEqual(speakers['Prof Kamau Njoroge']['rating_avg'], None)

    def test_revenue_only_for_sales_users(self):
        product = self.env['product.product'].create({
            'name': 'Summit pass', 'type': 'service', 'service_tracking': 'event', 'list_price': 5000, 'taxes_id': False})
        ticket = self.env['event.event.ticket'].create({
            'name': 'Standard', 'event_id': self.event.id, 'product_id': product.id, 'price': 5000})
        customer = self.env['res.partner'].create({'name': 'Acme Kenya', 'email': 'buy@acme.example.com'})
        order = self.env['sale.order'].create({
            'partner_id': customer.id,
            'order_line': [(0, 0, {'product_id': product.id, 'event_id': self.event.id, 'event_ticket_id': ticket.id,
                                   'product_uom_qty': 2, 'price_unit': 5000, 'tax_ids': False})],
        })
        order.action_confirm()
        data = self.event.get_conference_report_data()
        self.assertEqual(data['revenue']['total'], 10000)
        self.assertEqual(data['revenue']['tickets'][0]['name'], 'Standard')
        self.assertEqual(data['revenue']['tickets'][0]['seats'], 2)

        organiser = new_test_user(self.env, login='organiser_no_sales', groups='event.group_event_user')
        data = self.event.with_user(organiser).get_conference_report_data()
        self.assertIsNone(data['revenue'])
        self.assertEqual(data['kpis']['attended'], 2)

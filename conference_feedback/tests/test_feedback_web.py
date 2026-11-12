from datetime import timedelta

from odoo import fields, http
from odoo.tests import HttpCase, tagged


@tagged('post_install', '-at_install')
class TestFeedbackWeb(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        now = fields.Datetime.now()
        cls.event = cls.env['event.event'].create({
            'name': 'Web Feedback Summit',
            'date_begin': now - timedelta(days=1),
            'date_end': now - timedelta(hours=1),
            'cpd_enabled': True,
            'certificate_enabled': True,
        })
        cls.track = cls.env['event.track'].create({
            'name': 'Digital health keynote', 'event_id': cls.event.id,
            'date': now - timedelta(hours=5), 'duration': 1.0, 'cpd_points': 2,
        })
        cls.reg = cls.env['event.registration'].create({
            'event_id': cls.event.id, 'name': 'Zawadi Achieng', 'email': 'zawadi@example.com'})
        cls.reg.state = 'done'
        cls.env['conference.session.attendance'].create({'track_id': cls.track.id, 'registration_id': cls.reg.id})
        cls.feedback = cls.env['conference.session.feedback'].create({
            'track_id': cls.track.id, 'registration_id': cls.reg.id})
        cls.url = cls.reg._get_feedback_url().replace(cls.reg.get_base_url(), '')

    def test_feedback_page_and_submit(self):
        res = self.url_open(self.url)
        self.assertEqual(res.status_code, 200)
        self.assertIn('Digital health keynote', res.text)
        self.assertIn('Save my ratings', res.text)

        self.authenticate(None, None)  # a public session, for the CSRF token
        res = self.url_open(self.url, data={
            'csrf_token': http.Request.csrf_token(self),
            'rating_%s' % self.feedback.id: '4',
            'comment_%s' % self.feedback.id: 'Very useful',
        })
        self.assertEqual(res.status_code, 200)
        self.assertIn('Your ratings are saved', res.text)
        self.assertEqual((self.feedback.state, self.feedback.rating, self.feedback.comment), ('done', 4, 'Very useful'))

    def test_feedback_closes_after_30_days(self):
        self.event.write({'date_begin': fields.Datetime.now() - timedelta(days=33),
                          'date_end': fields.Datetime.now() - timedelta(days=31)})
        res = self.url_open(self.url)
        self.assertIn('Feedback for this event has closed', res.text)
        self.authenticate(None, None)
        self.url_open(self.url, data={'csrf_token': http.Request.csrf_token(self), 'rating_%s' % self.feedback.id: '2'})
        self.assertEqual(self.feedback.state, 'pending')

    def test_bad_token(self):
        self.assertEqual(self.url_open('/conference/feedback/not-a-real-token-123456').status_code, 404)
        self.assertEqual(self.url_open('/conference/feedback/short').status_code, 404)

    def test_certificate_verify_and_download(self):
        cert = self.reg._certificate_issue(send=False)
        self.assertIn('Download certificate', self.url_open(self.url).text)

        path = cert._get_verify_url().replace(cert.get_base_url(), '')
        res = self.url_open(path)
        self.assertIn('is genuine', res.text)
        self.assertIn('Zawadi Achieng', res.text)

        res = self.url_open(path + '/pdf')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers['Content-Type'], 'application/pdf')

        res = self.url_open('/conference/certificate/%s/wrongcode1234' % cert.name)
        self.assertIn('could not verify', res.text)
        self.assertNotIn('Zawadi', res.text)

        cert.action_revoke()
        res = self.url_open(path)
        self.assertIn('has been revoked', res.text)
        self.assertEqual(self.url_open(path + '/pdf').status_code, 404)

    def test_event_report_tour(self):
        self.env['conference.session.feedback'].search([]).filtered(lambda f: f.track_id == self.track)._record_answer(5)
        self.start_tour('/odoo/action-event.action_event_view/%s' % self.event.id,
                        'conference_feedback_event_report', login='admin')

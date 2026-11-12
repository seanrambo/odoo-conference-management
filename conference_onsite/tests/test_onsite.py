from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestOnsite(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.now = fields.Datetime.now().replace(microsecond=0)
        cls.event = cls.env['event.event'].create({
            'name': 'Nairobi Health Summit',
            'date_begin': cls.now - timedelta(hours=2),
            'date_end': cls.now + timedelta(days=1),
            'date_tz': 'Africa/Nairobi',
            'cpd_enabled': True,
        })
        cls.other_event = cls.env['event.event'].create({
            'name': 'Mombasa Forum',
            'date_begin': cls.now - timedelta(hours=2),
            'date_end': cls.now + timedelta(days=1),
        })
        cls.hall_a = cls.env['event.track.location'].create({'name': 'Hall A'})
        cls.hall_b = cls.env['event.track.location'].create({'name': 'Hall B'})
        cls.stage_published = cls.env['event.track.stage'].create({'name': 'Published', 'is_visible_in_agenda': True})
        cls.stage_cancel = cls.env['event.track.stage'].create({'name': 'Cancelled', 'is_cancel': True})
        cls.speaker = cls.env['res.partner'].create({'name': 'Dr Achieng Otieno', 'email': 'achieng@example.com'})
        cls.live_track = cls._track(cls, 'Opening keynote', cls.now - timedelta(minutes=10), 1.0, cpd_points=1.5)
        cls.later_track = cls._track(cls, 'Malaria research update', cls.now + timedelta(hours=5), 1.0, cpd_points=2.0)
        cls.reg = cls._register(cls, cls.event, 'Wanjiku Mwangi', 'wanjiku@example.com')
        cls.reg_other = cls._register(cls, cls.other_event, 'Otieno Ouma', 'otieno@example.com')
        cls.Attendance = cls.env['conference.session.attendance']

    def _track(self, name, date, duration, **vals):
        return self.env['event.track'].create(dict({
            'name': name,
            'event_id': self.event.id,
            'date': date,
            'duration': duration,
            'location_id': self.hall_a.id,
            'stage_id': self.stage_published.id,
            'partner_id': self.speaker.id,
            'is_published': True,
        }, **vals))

    def _register(self, event, name, email, state='open'):
        reg = self.env['event.registration'].create({'event_id': event.id, 'name': name, 'email': email})
        if state != reg.state:
            reg.state = state
        return reg

    # ------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------

    def test_scan_records_attendance_and_checks_in(self):
        self.assertEqual(self.reg.state, 'open')
        res = self.Attendance.scan(self.live_track.id, barcode=self.reg.barcode)
        self.assertEqual(res['status'], 'recorded')
        self.assertTrue(res['success'])
        self.assertEqual(res['attendee']['name'], 'Wanjiku Mwangi')
        self.assertEqual(res['session']['count'], 1)
        attendance = self.reg.onsite_attendance_ids
        self.assertEqual(attendance.track_id, self.live_track)
        self.assertEqual(attendance.method, 'scan')
        self.assertEqual(attendance.cpd_points, 1.5)
        self.assertEqual(self.reg.state, 'done', "A session scan also checks the attendee in to the event")
        self.assertEqual(self.reg.cpd_points_total, 1.5)
        self.assertEqual(self.live_track.onsite_attendance_count, 1)

    def test_scan_without_event_checkin(self):
        self.event.onsite_session_checkin = False
        res = self.Attendance.scan(self.live_track.id, barcode=self.reg.barcode)
        self.assertEqual(res['status'], 'recorded')
        self.assertEqual(self.reg.state, 'open')

    def test_scan_twice_is_flagged(self):
        self.Attendance.scan(self.live_track.id, barcode=self.reg.barcode)
        res = self.Attendance.scan(self.live_track.id, barcode=self.reg.barcode)
        self.assertEqual(res['status'], 'already_recorded')
        self.assertFalse(res['can_force'])
        self.assertEqual(len(self.reg.onsite_attendance_ids), 1)

    def test_scan_rejections(self):
        res = self.Attendance.scan(self.live_track.id, barcode='0000000000000')
        self.assertEqual(res['status'], 'invalid_ticket')

        res = self.Attendance.scan(self.live_track.id, barcode=self.reg_other.barcode)
        self.assertEqual(res['status'], 'wrong_event')
        self.assertIn('Mombasa Forum', res['message'])

        draft = self._register(self.event, 'Draft Person', 'draft@example.com', state='draft')
        self.assertEqual(self.Attendance.scan(self.live_track.id, barcode=draft.barcode)['status'],
                         'unconfirmed_registration')

        cancelled = self._register(self.event, 'Gone Person', 'gone@example.com', state='cancel')
        self.assertEqual(self.Attendance.scan(self.live_track.id, barcode=cancelled.barcode)['status'],
                         'cancelled_registration')

        self.live_track.with_context(onsite_skip_change_notify=True).stage_id = self.stage_cancel
        self.assertEqual(self.Attendance.scan(self.live_track.id, barcode=self.reg.barcode)['status'],
                         'session_cancelled')
        self.assertFalse(self.Attendance.search([('event_id', '=', self.event.id)]))

    def test_scan_outside_window_needs_override(self):
        res = self.Attendance.scan(self.later_track.id, barcode=self.reg.barcode)
        self.assertEqual(res['status'], 'outside_window')
        self.assertTrue(res['can_force'])
        self.assertFalse(self.reg.onsite_attendance_ids)

        res = self.Attendance.scan(self.later_track.id, barcode=self.reg.barcode, force=True)
        self.assertEqual(res['status'], 'recorded')
        self.assertEqual(self.reg.onsite_attendance_ids.override_reason, 'outside_window')

    def test_scan_window_margin(self):
        soon = self._track('Coffee briefing', self.now + timedelta(minutes=20), 0.5)
        self.assertEqual(self.Attendance.scan(soon.id, barcode=self.reg.barcode)['status'], 'recorded',
                         "20 minutes early is inside the default 30-minute window")
        self.event.onsite_scan_margin = 10
        reg2 = self._register(self.event, 'Kamau Njoroge', 'kamau@example.com')
        self.assertEqual(self.Attendance.scan(soon.id, barcode=reg2.barcode)['status'], 'outside_window')

    def test_room_full_needs_override(self):
        self.live_track.onsite_seat_limit = 1
        self.Attendance.scan(self.live_track.id, barcode=self.reg.barcode)
        reg2 = self._register(self.event, 'Kamau Njoroge', 'kamau@example.com')
        res = self.Attendance.scan(self.live_track.id, barcode=reg2.barcode)
        self.assertEqual(res['status'], 'session_full')
        self.assertTrue(res['can_force'])
        res = self.Attendance.scan(self.live_track.id, barcode=reg2.barcode, force=True)
        self.assertEqual(res['status'], 'recorded')
        self.assertEqual(res['session']['count'], 2)
        self.assertEqual(reg2.onsite_attendance_ids.override_reason, 'session_full')

    def test_manual_record_and_search(self):
        found = self.Attendance.scanner_search(self.event.id, 'wanjiku')
        self.assertEqual([r['id'] for r in found], [self.reg.id])
        self.assertFalse(self.Attendance.scanner_search(self.event.id, 'w'), "Needs at least two characters")
        self.assertFalse(self.Attendance.scanner_search(self.event.id, 'otieno'), "Other events are not searched")

        res = self.Attendance.scan(self.live_track.id, registration_id=self.reg.id)
        self.assertEqual(res['status'], 'recorded')
        self.assertEqual(self.reg.onsite_attendance_ids.method, 'manual')

    def test_cpd_total_across_sessions(self):
        self.Attendance.scan(self.live_track.id, barcode=self.reg.barcode)
        self.Attendance.scan(self.later_track.id, barcode=self.reg.barcode, force=True)
        self.assertEqual(self.reg.onsite_session_count, 2)
        self.assertEqual(self.reg.cpd_points_total, 3.5)
        # Points are fixed at scan time: later changes to the session don't rewrite history.
        self.live_track.cpd_points = 5
        self.assertEqual(self.reg.cpd_points_total, 3.5)

    def test_scanner_init_and_counts(self):
        cancelled = self._track('Dropped panel', self.now + timedelta(hours=1), 1.0)
        cancelled.with_context(onsite_skip_change_notify=True).stage_id = self.stage_cancel
        data = self.Attendance.scanner_init(self.event.id)
        self.assertEqual(data['default_session_id'], self.live_track.id, "The live session is picked by default")
        self.assertEqual([s['id'] for s in data['sessions']], [self.live_track.id, cancelled.id, self.later_track.id])
        self.assertTrue(next(s for s in data['sessions'] if s['id'] == cancelled.id)['is_cancelled'])
        self.assertEqual(data['expected_count'], 1)
        self.assertEqual(data['checkin_count'], 0)

        self.Attendance.scan(self.live_track.id, barcode=self.reg.barcode)
        self.event.invalidate_recordset()
        self.assertEqual(self.event.onsite_checkin_count, 1)
        self.assertEqual(self.event.onsite_checkin_rate, 100.0)
        self.assertEqual(self.event.onsite_session_attendance_count, 1)
        self.assertIn(self.event.id, [e['id'] for e in self.Attendance.scanner_events()])

    def test_registration_desk_user_can_scan(self):
        desk = new_test_user(self.env, login='door_staff', groups='event.group_event_registration_desk')
        Attendance = self.Attendance.with_user(desk)
        data = Attendance.scanner_init(self.event.id)
        self.assertTrue(data['sessions'])
        res = Attendance.scan(self.live_track.id, barcode=self.reg.barcode)
        self.assertEqual(res['status'], 'recorded')
        self.assertEqual(self.reg.onsite_attendance_ids.user_id, desk)
        menu = self.env.ref('conference_onsite.menu_conference_session_scanner')
        self.assertIn(menu.id, self.env['ir.ui.menu'].with_user(desk)._visible_menu_ids())

    # ------------------------------------------------------------
    # Session change emails
    # ------------------------------------------------------------

    def _wishlist(self, track, partner, **vals):
        return self.env['event.track.visitor'].sudo().create(dict(
            {'track_id': track.id, 'partner_id': partner.id, 'is_wishlisted': True}, **vals))

    def _mails(self, track):
        return self.env['mail.mail'].sudo().search([('model', '=', 'event.track'), ('res_id', '=', track.id)])

    def test_time_and_room_change_emails_followers(self):
        fan = self.env['res.partner'].create({'name': 'Amina Hassan', 'email': 'amina@example.com'})
        opted_out = self.env['res.partner'].create({'name': 'Baraka Kip', 'email': 'baraka@example.com'})
        self._wishlist(self.later_track, fan)
        self._wishlist(self.later_track, opted_out, is_blacklisted=True)

        self.later_track.write({
            'date': self.later_track.date + timedelta(hours=1),
            'location_id': self.hall_b.id,
        })
        mails = self._mails(self.later_track)
        self.assertEqual(sorted(mails.mapped('email_to')), sorted([
            '"Amina Hassan" <amina@example.com>', '"Dr Achieng Otieno" <achieng@example.com>']))
        self.assertTrue(all(m.subject.startswith('Change to Malaria research update') for m in mails))
        body = str(mails[0].body_html)
        self.assertIn('Hall A', body)
        self.assertIn('Hall B', body)
        self.assertIn('Room', body)
        self.assertIn('Time', body)
        self.assertIn('Change emailed to 2 people', self.later_track.message_ids[0].body)

    def test_key_session_emails_all_attendees(self):
        self.later_track.wishlisted_by_default = True
        self._register(self.event, 'Kamau Njoroge', 'kamau@example.com')
        self.later_track.location_id = self.hall_b
        self.assertEqual(sorted(self._mails(self.later_track).mapped('email_to')), sorted([
            '"Wanjiku Mwangi" <wanjiku@example.com>', '"Kamau Njoroge" <kamau@example.com>',
            '"Dr Achieng Otieno" <achieng@example.com>']))

    def test_cancellation_email(self):
        fan = self.env['res.partner'].create({'name': 'Amina Hassan', 'email': 'amina@example.com'})
        self._wishlist(self.later_track, fan)
        self.later_track.stage_id = self.stage_cancel
        mails = self._mails(self.later_track)
        self.assertEqual(len(mails), 2)
        self.assertTrue(all(m.subject.startswith('Cancelled: Malaria research update') for m in mails))
        self.assertIn('has been cancelled', str(mails[0].body_html))
        # Moving an already-cancelled session around doesn't email anyone again.
        self.later_track.location_id = self.hall_b
        self.assertEqual(len(self._mails(self.later_track)), 2)

    def test_no_email_when_nothing_relevant_changed(self):
        fan = self.env['res.partner'].create({'name': 'Amina Hassan', 'email': 'amina@example.com'})
        self._wishlist(self.later_track, fan)
        self.later_track.write({'date': self.later_track.date, 'location_id': self.hall_a.id})
        self.later_track.name = 'Malaria research: 2026 update'
        self.assertFalse(self._mails(self.later_track))

    def test_no_email_when_disabled_unpublished_or_past(self):
        fan = self.env['res.partner'].create({'name': 'Amina Hassan', 'email': 'amina@example.com'})
        self._wishlist(self.later_track, fan)

        self.event.onsite_notify_changes = False
        self.later_track.location_id = self.hall_b
        self.assertFalse(self._mails(self.later_track))
        self.event.onsite_notify_changes = True

        self.later_track.is_published = False
        self.later_track.location_id = self.hall_a
        self.assertFalse(self._mails(self.later_track))

        past = self._track('Yesterday workshop', self.now - timedelta(hours=3), 1.0)
        self._wishlist(past, fan)
        past.location_id = self.hall_b
        self.assertFalse(self._mails(past))

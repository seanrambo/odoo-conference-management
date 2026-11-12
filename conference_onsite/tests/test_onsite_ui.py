from datetime import timedelta

from odoo import fields
from odoo.tests import HttpCase, new_test_user, tagged

TOUR_BARCODE = '424242424242'  # must match session_scanner_tour.js


@tagged('post_install', '-at_install')
class TestSessionScannerUI(HttpCase):

    def test_session_scanner_tour(self):
        now = fields.Datetime.now()
        event = self.env['event.event'].create({
            'name': 'Scanner Tour Summit',
            'date_begin': now - timedelta(hours=1),
            'date_end': now + timedelta(days=1),
        })
        track = self.env['event.track'].create({
            'name': 'Tour keynote', 'event_id': event.id,
            'date': now - timedelta(minutes=5), 'duration': 1.0,
        })
        registration = self.env['event.registration'].create({
            'event_id': event.id, 'name': 'Zawadi Achieng', 'email': 'zawadi@example.com',
        })
        registration.barcode = TOUR_BARCODE
        new_test_user(self.env, login='door_staff', groups='event.group_event_registration_desk')

        self.start_tour('/odoo/session-scanner', 'conference_onsite_session_scanner', login='door_staff')

        self.assertEqual(track.onsite_attendance_ids.registration_id, registration)
        self.assertEqual(track.onsite_attendance_ids.method, 'manual')
        self.assertEqual(registration.state, 'done')

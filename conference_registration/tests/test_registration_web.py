from datetime import timedelta

from odoo import fields, http
from odoo.tests import HttpCase, new_test_user, tagged


@tagged('post_install', '-at_install', 'conference')
class TestRegistrationWeb(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        now = fields.Datetime.now()
        cls.event = cls.env['event.event'].create({
            'name': 'Mombasa Forum',
            'date_begin': now + timedelta(days=20),
            'date_end': now + timedelta(days=21),
            'seats_limited': True,
            'seats_max': 1,
            'website_published': True,
            'event_ticket_ids': [(5, 0)],
            'visa_letter_enabled': True,
        })
        cls.reg = cls.env['event.registration'].create({
            'event_id': cls.event.id, 'name': 'Amina', 'email': 'amina@example.com', 'state': 'open'})

    def _post(self, url, data):
        if not self.session:
            self.authenticate(None, None)
        return self.url_open(url, data=dict(data, csrf_token=http.Request.csrf_token(self)))

    def test_sold_out_page_offers_waitlist(self):
        res = self.url_open('/event/%s/register' % self.event.id)
        self.assertEqual(res.status_code, 200)
        self.assertIn('/event/%s/waitlist' % self.event.id, res.text)

        res = self.url_open('/event/%s/waitlist' % self.event.id)
        self.assertEqual(res.status_code, 200)
        res = self._post('/event/%s/waitlist' % self.event.id,
                         {'name': 'Baraka', 'email': 'baraka@example.com', 'phone': ''})
        self.assertEqual(res.status_code, 200)
        entry = self.env['conference.waitlist'].search([('email', '=', 'baraka@example.com')])
        self.assertEqual(entry.state, 'waiting')
        self.assertIn('Your position', res.text)

        # Bots filling the hidden field are ignored.
        self._post('/event/%s/waitlist' % self.event.id,
                   {'name': 'Bot', 'email': 'bot@example.com', 'website_url': 'http://spam'})
        self.assertFalse(self.env['conference.waitlist'].search([('email', '=', 'bot@example.com')]))

    def test_booking_page_transfer_and_visa(self):
        token = self.reg.conference_token
        res = self.url_open('/event/booking/%s' % token)
        self.assertEqual(res.status_code, 200)
        self.assertIn('Download ticket', res.text)

        res = self._post('/event/booking/%s/visa' % token, {
            'visa_full_name': 'AMINA W', 'visa_passport_number': 'K123',
            'visa_nationality_id': self.env.ref('base.gh').id,
            'visa_date_of_birth': '1991-02-03',
            'visa_passport_expiry': str(fields.Date.today() + timedelta(days=500)),
        })
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.reg.visa_letter_state, 'requested')
        self.reg.action_visa_issue()
        res = self.url_open('/event/booking/%s/visa-letter' % token)
        self.assertEqual(res.status_code, 200)

        verify = self.reg._get_visa_verify_url().replace(self.reg.get_base_url(), '')
        res = self.url_open(verify)
        self.assertIn('is genuine', res.text)
        res = self.url_open('/event/visa-letter/verify/%s/wrongcode' % self.reg.visa_letter_reference)
        self.assertIn('could not verify', res.text)

        res = self._post('/event/booking/%s/transfer' % token, {'name': 'Chen', 'email': 'chen@example.com'})
        self.assertIn('Ticket transferred', res.text)
        self.assertEqual(self.reg.email, 'chen@example.com')
        res = self.url_open('/event/booking/%s' % token)
        self.assertIn('no longer works', res.text)
        res = self.url_open(verify)
        self.assertIn('could not verify', res.text, "A transferred ticket's letter is withdrawn")

    def test_portal_tickets(self):
        new_test_user(self.env, login='cr_portal', password='cr_portal', groups='base.group_portal',
                      email='amina@example.com')
        self.authenticate('cr_portal', 'cr_portal')
        res = self.url_open('/my/tickets')
        self.assertEqual(res.status_code, 200)
        self.assertIn('Mombasa Forum', res.text)
        self.assertEqual(self.url_open('/my').status_code, 200)

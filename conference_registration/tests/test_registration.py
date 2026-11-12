from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged


class RegistrationCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        now = fields.Datetime.now()
        cls.product = cls.env['product.product'].create({
            'name': 'Conference pass', 'type': 'service', 'service_tracking': 'event', 'list_price': 5000,
        })
        cls.company_partner = cls.env['res.partner'].create({
            'name': 'Acme Kenya Ltd', 'is_company': True, 'email': 'accounts@acme.example.com'})

    def _event(self, seats=2, paid=True, **vals):
        now = fields.Datetime.now()
        event = self.env['event.event'].create(dict({
            'name': 'Nairobi Summit',
            'date_begin': now + timedelta(days=10),
            'date_end': now + timedelta(days=11),
            'seats_limited': True,
            'seats_max': seats,
            'event_ticket_ids': [(5, 0)],
        }, **vals))
        ticket = self.env['event.event.ticket']
        if paid:
            ticket = self.env['event.event.ticket'].create({
                'name': 'Standard', 'event_id': event.id, 'product_id': self.product.id, 'price': 5000,
            })
        return event, ticket

    def _register(self, event, ticket, name, email):
        return self.env['event.registration'].create({
            'event_id': event.id, 'event_ticket_id': ticket.id, 'name': name, 'email': email,
            'state': 'open',
        })

    def _join(self, event, ticket, name, email):
        return self.env['conference.waitlist'].create({
            'event_id': event.id, 'event_ticket_id': ticket.id, 'name': name, 'email': email})


@tagged('post_install', '-at_install', 'conference')
class TestWaitlist(RegistrationCase):

    def test_paid_seat_is_held_then_booked(self):
        event, ticket = self._event(seats=2)
        reg1 = self._register(event, ticket, 'Reg One', 'one@example.com')
        self._register(event, ticket, 'Reg Two', 'two@example.com')
        self.assertTrue(event.event_registrations_sold_out)

        a = self._join(event, ticket, 'Amina', 'amina@example.com')
        b = self._join(event, ticket, 'Baraka', 'baraka@example.com')
        self.assertEqual((a.position, b.position), (1, 2))

        reg1.action_cancel()
        self.assertEqual(a.state, 'offered')
        self.assertEqual(b.state, 'waiting')
        self.assertTrue(a.offer_deadline > fields.Datetime.now())
        self.assertEqual(event.seats_available, 0, "The freed seat is held for Amina")
        self.assertTrue(event.event_registrations_sold_out, "Nobody else can take the held seat")

        order = a._get_or_create_order()
        self.assertEqual(order.partner_id.email, 'amina@example.com')
        self.assertTrue(order.require_payment)
        self.assertFalse(order._has_to_be_signed(), "Paying online must be enough to confirm")
        self.assertEqual(a._get_or_create_order(), order, "Claiming twice reuses the same quotation")
        order.action_confirm()
        self.assertEqual(a.state, 'booked')
        self.assertEqual(a.registration_id.email, 'amina@example.com')
        self.assertEqual(a.registration_id.name, 'Amina')
        self.assertEqual(a.registration_id.state, 'open')
        self.assertEqual(event.seats_available, 0)
        self.assertEqual(b.state, 'waiting')

    def test_offer_expires_and_moves_on(self):
        event, ticket = self._event(seats=1)
        reg = self._register(event, ticket, 'Reg', 'reg@example.com')
        a = self._join(event, ticket, 'Amina', 'amina@example.com')
        b = self._join(event, ticket, 'Baraka', 'baraka@example.com')
        reg.action_cancel()
        order = a._get_or_create_order()
        a.offer_deadline = fields.Datetime.now() - timedelta(minutes=1)
        self.env['conference.waitlist']._cron_process_waitlists()
        self.assertEqual(a.state, 'expired')
        self.assertEqual(order.state, 'cancel')
        self.assertEqual(b.state, 'offered')
        with self.assertRaises(UserError):
            a._get_or_create_order()

    def test_free_seat_is_booked_directly(self):
        event, _ticket = self._event(seats=1, paid=False)
        reg = self._register(event, _ticket, 'Reg', 'reg@example.com')
        a = self._join(event, _ticket, 'Amina', 'amina@example.com')
        reg.action_cancel()
        self.assertEqual(a.state, 'booked')
        self.assertEqual(a.registration_id.state, 'open')
        self.assertEqual(event.seats_available, 0)

    def test_more_seats_promotes(self):
        event, ticket = self._event(seats=1)
        self._register(event, ticket, 'Reg', 'reg@example.com')
        a = self._join(event, ticket, 'Amina', 'amina@example.com')
        b = self._join(event, ticket, 'Baraka', 'baraka@example.com')
        event.seats_max = 3
        self.assertEqual((a.state, b.state), ('offered', 'offered'))

    def test_leaving_releases_offer(self):
        event, ticket = self._event(seats=1)
        reg = self._register(event, ticket, 'Reg', 'reg@example.com')
        a = self._join(event, ticket, 'Amina', 'amina@example.com')
        b = self._join(event, ticket, 'Baraka', 'baraka@example.com')
        reg.action_cancel()
        a.action_cancel()
        self.assertEqual(a.state, 'cancelled')
        self.assertEqual(b.state, 'offered')

    def test_no_duplicates_and_valid_email(self):
        event, ticket = self._event()
        self._join(event, ticket, 'Amina', 'Amina@Example.com')
        with self.assertRaises(UserError):
            self._join(event, ticket, 'Amina again', 'amina@example.com')
        with self.assertRaises(UserError):
            self._join(event, ticket, 'Bad', 'not-an-email')


@tagged('post_install', '-at_install', 'conference')
class TestSelfService(RegistrationCase):

    def test_transfer(self):
        event, ticket = self._event(seats=10, paid=False, visa_letter_enabled=True)
        reg = self._register(event, ticket, 'Amina', 'amina@example.com')
        reg.write({'visa_full_name': 'AMINA X', 'visa_passport_number': 'A123', 'visa_letter_state': 'requested'})
        old_barcode, old_token = reg.barcode, reg.conference_token
        self.assertTrue(old_token)

        reg._conference_transfer('Baraka', 'Baraka@example.com', '0712345678')
        self.assertEqual((reg.name, reg.email), ('Baraka', 'baraka@example.com'))
        self.assertNotEqual(reg.barcode, old_barcode, "The old ticket no longer scans")
        self.assertNotEqual(reg.conference_token, old_token, "The old booking link stops working")
        self.assertFalse(reg.visa_passport_number, "Passport data of the previous holder is cleared")
        self.assertEqual(reg.visa_letter_state, 'none')
        self.assertEqual(reg.transfer_count, 1)

        with self.assertRaises(UserError):
            reg._conference_transfer('Baraka', 'baraka@example.com')
        with self.assertRaises(UserError):
            reg._conference_transfer('', 'x@example.com')

        event.date_begin = fields.Datetime.now() + timedelta(hours=2)
        self.assertFalse(reg._conference_can_transfer())
        with self.assertRaises(UserError):
            reg._conference_transfer('Chen', 'chen@example.com')

    def test_cancel_free_promotes_waitlist(self):
        event, ticket = self._event(seats=1, paid=False)
        reg = self._register(event, ticket, 'Amina', 'amina@example.com')
        waiting = self._join(event, ticket, 'Baraka', 'baraka@example.com')
        reg._conference_self_cancel()
        self.assertEqual(reg.state, 'cancel')
        self.assertFalse(reg.refund_requested)
        self.assertEqual(waiting.state, 'booked')

    def test_cancel_deadline(self):
        event, ticket = self._event(seats=5, paid=False, cancel_deadline_hours=72)
        event.date_begin = fields.Datetime.now() + timedelta(hours=48)
        reg = self._register(event, ticket, 'Amina', 'amina@example.com')
        with self.assertRaises(UserError):
            reg._conference_self_cancel()

    def test_cancel_paid_requests_refund(self):
        event, ticket = self._event(seats=5)
        wizard = self.env['conference.group.booking'].create({
            'event_id': event.id, 'partner_id': self.company_partner.id, 'default_ticket_id': ticket.id,
            'attendees_text': 'Amina, amina@example.com', 'create_invoice': False,
        })
        wizard.action_book()
        reg = event.registration_ids
        self.assertTrue(reg._is_paid_booking())
        reg._conference_self_cancel()
        self.assertEqual(reg.state, 'cancel')
        self.assertTrue(reg.refund_requested)
        self.assertTrue(reg.activity_ids.filtered(lambda a: a.summary == 'Refund requested'))


@tagged('post_install', '-at_install', 'conference')
class TestVisaLetters(RegistrationCase):

    def _visa_vals(self, **over):
        return dict({
            'visa_full_name': 'AMINA WANJIRU',
            'visa_passport_number': 'ak1234567',
            'visa_nationality_id': self.env.ref('base.ng').id,
            'visa_date_of_birth': fields.Date.to_date('1990-05-01'),
            'visa_passport_expiry': fields.Date.today() + timedelta(days=400),
        }, **over)

    def test_request_review_issue_verify(self):
        event, ticket = self._event(seats=5, paid=False, visa_letter_enabled=True,
                                    visa_signatory_name='Jane Doe', visa_signatory_title='Programme Chair')
        reg = self._register(event, ticket, 'Amina', 'amina@example.com')
        self.assertTrue(reg._conference_can_request_visa())
        reg._conference_visa_request(self._visa_vals())
        self.assertEqual(reg.visa_letter_state, 'requested')
        self.assertEqual(reg.visa_passport_number, 'AK1234567')
        self.assertTrue(reg.activity_ids)

        reg.action_visa_issue()
        self.assertEqual(reg.visa_letter_state, 'issued')
        self.assertTrue(reg.visa_letter_reference.startswith('VL'))
        self.assertFalse(reg.activity_ids)
        self.assertEqual(reg._visa_masked_passport()[-3:], '567')
        self.assertTrue(reg._get_visa_verify_url().endswith(reg._visa_verification_code()))

        html = self.env['ir.actions.report']._render_qweb_html(
            'conference_registration.action_report_visa_letter', reg.ids)[0].decode()
        for text in ('AMINA WANJIRU', 'AK1234567', 'Nigeria', 'Jane Doe', reg.visa_letter_reference):
            self.assertIn(text, html)

        mail = self.env['mail.mail'].search([('model', '=', 'event.registration'), ('res_id', '=', reg.id),
                                             ('subject', 'ilike', 'invitation letter')], limit=1)
        self.assertTrue(mail.attachment_ids, "The letter is attached to the email")

    def test_request_rules(self):
        event, ticket = self._event(seats=5, paid=False, visa_letter_enabled=True)
        reg = self._register(event, ticket, 'Amina', 'amina@example.com')
        with self.assertRaises(UserError):
            reg._conference_visa_request(self._visa_vals(visa_passport_number=''))
        with self.assertRaises(UserError):
            reg._conference_visa_request(self._visa_vals(visa_passport_expiry=fields.Date.today()))
        event.visa_letter_enabled = False
        with self.assertRaises(UserError):
            reg._conference_visa_request(self._visa_vals())

    def test_auto_issue_and_reject(self):
        event, ticket = self._event(seats=5, paid=False, visa_letter_enabled=True, visa_letter_auto_issue=True)
        reg = self._register(event, ticket, 'Amina', 'amina@example.com')
        reg._conference_visa_request(self._visa_vals())
        self.assertEqual(reg.visa_letter_state, 'issued')

        event.visa_letter_auto_issue = False
        other = self._register(event, ticket, 'Baraka', 'baraka@example.com')
        other._conference_visa_request(self._visa_vals())
        with self.assertRaises(UserError):
            other.action_visa_reject()
        other.visa_reject_reason = 'Passport scan unreadable'
        other.action_visa_reject()
        self.assertEqual(other.visa_letter_state, 'rejected')
        self.assertTrue(other._conference_can_request_visa(), "The attendee can try again")


@tagged('post_install', '-at_install', 'conference')
class TestGroupBooking(RegistrationCase):

    def test_group_booking_order_and_invoice(self):
        event, ticket = self._event(seats=10)
        wizard = self.env['conference.group.booking'].create({
            'event_id': event.id, 'partner_id': self.company_partner.id, 'default_ticket_id': ticket.id,
            'attendees_text': 'Name, Email, Phone\nAmina W, amina@example.com, 0712000001\n'
                              'Baraka O, BARAKA@example.com\nChen L, chen@example.com, 0712000003',
        })
        action = wizard.action_book()
        self.assertEqual(action['res_model'], 'account.move')
        invoice = self.env['account.move'].browse(action['res_id'])
        self.assertEqual(invoice.partner_id, self.company_partner)
        self.assertEqual(invoice.amount_untaxed, 15000)

        regs = event.registration_ids.sorted('id')
        self.assertEqual(regs.mapped('name'), ['Amina W', 'Baraka O', 'Chen L'])
        self.assertEqual(regs.mapped('email'), ['amina@example.com', 'baraka@example.com', 'chen@example.com'])
        self.assertEqual(set(regs.mapped('state')), {'open'})
        self.assertEqual(len(regs.sale_order_id), 1)
        self.assertEqual(event.seats_available, 7)

    def test_group_booking_errors(self):
        event, ticket = self._event(seats=2)
        Wizard = self.env['conference.group.booking']
        with self.assertRaises(UserError):
            Wizard.create({'event_id': event.id, 'partner_id': self.company_partner.id,
                           'attendees_text': 'Amina, not-an-email'}).action_book()
        with self.assertRaises(UserError):
            Wizard.create({'event_id': event.id, 'partner_id': self.company_partner.id, 'default_ticket_id': ticket.id,
                           'attendees_text': 'A, a@example.com\nB, a@example.com'}).action_book()
        with self.assertRaises(ValidationError):
            Wizard.create({'event_id': event.id, 'partner_id': self.company_partner.id, 'default_ticket_id': ticket.id,
                           'attendees_text': 'A, a@example.com\nB, b@example.com\nC, c@example.com'}).action_book()

    def test_new_events_send_booking_link(self):
        event, _ticket = self._event(paid=False)
        templates = event.event_mail_ids.mapped('template_ref')
        self.assertIn(self.env.ref('conference_registration.mail_template_manage_booking'), templates)

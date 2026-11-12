import uuid
from datetime import timedelta

from odoo import _, api, fields, models, tools
from odoo.exceptions import UserError
from odoo.tools import email_normalize

VISA_STATES = [
    ('none', 'Not Requested'),
    ('requested', 'Requested'),
    ('issued', 'Issued'),
    ('rejected', 'Rejected'),
]
VISA_FIELDS = ('visa_full_name', 'visa_passport_number', 'visa_nationality_id',
               'visa_date_of_birth', 'visa_passport_expiry')


class EventRegistration(models.Model):
    _inherit = 'event.registration'

    conference_token = fields.Char('Booking Token', readonly=True, copy=False, index='btree_not_null')
    transfer_count = fields.Integer('Transfers', readonly=True, copy=False)
    refund_requested = fields.Boolean('Refund Requested', readonly=True, copy=False, tracking=True)

    visa_full_name = fields.Char('Name as in Passport', copy=False)
    visa_passport_number = fields.Char('Passport Number', copy=False)
    visa_nationality_id = fields.Many2one('res.country', 'Nationality', copy=False)
    visa_date_of_birth = fields.Date('Date of Birth', copy=False)
    visa_passport_expiry = fields.Date('Passport Expiry', copy=False)
    visa_letter_state = fields.Selection(VISA_STATES, 'Visa Letter', default='none', required=True,
                                         copy=False, tracking=True, index=True)
    visa_letter_reference = fields.Char('Letter Reference', readonly=True, copy=False)
    visa_letter_date = fields.Date('Letter Date', readonly=True, copy=False)
    visa_reject_reason = fields.Text('Rejection Reason', copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            vals.setdefault('conference_token', uuid.uuid4().hex)
        return super().create(vals_list)

    def write(self, vals):
        freeing = vals.get('state') == 'cancel' or vals.get('active') is False
        events = self.event_id if freeing else self.env['event.event']
        res = super().write(vals)
        if freeing:
            events._conference_waitlist_promote()
        return res

    def unlink(self):
        events = self.event_id
        res = super().unlink()
        events.exists()._conference_waitlist_promote()
        return res

    # ------------------------------------------------------------
    # Booking page
    # ------------------------------------------------------------

    def _conference_ensure_token(self):
        for registration in self.filtered(lambda r: not r.conference_token):
            registration.sudo().conference_token = uuid.uuid4().hex
        return self.conference_token if len(self) == 1 else None

    def _get_manage_url(self):
        self.ensure_one()
        return '%s/event/booking/%s' % (self.get_base_url(), self._conference_ensure_token())

    def _get_ticket_url(self):
        self.ensure_one()
        event = self.event_id
        return '%s/event/%s/my_tickets?registration_ids=%s&tickets_hash=%s' % (
            self.get_base_url(), event.id, [self.id], event._get_tickets_access_hash([self.id]))

    def _hours_to_start(self):
        self.ensure_one()
        start = self.event_slot_id.start_datetime if self.event_slot_id else self.event_id.date_begin
        if not start:
            return float('inf')
        return (start - fields.Datetime.now()).total_seconds() / 3600

    def _is_paid_booking(self):
        self.ensure_one()
        return self.sale_status == 'sold' and bool(self.sale_order_line_id) and \
            not self.sale_order_line_id.currency_id.is_zero(self.sale_order_line_id.price_total)

    def _conference_can_transfer(self):
        self.ensure_one()
        event = self.event_id
        return bool(event.transfer_enabled and self.state == 'open'
                    and self._hours_to_start() >= (event.transfer_deadline_hours or 0))

    def _conference_can_cancel(self):
        self.ensure_one()
        event = self.event_id
        return bool(event.self_cancel_enabled and self.state in ('draft', 'open')
                    and self._hours_to_start() >= (event.cancel_deadline_hours or 0))

    def _conference_can_request_visa(self):
        self.ensure_one()
        return bool(self.event_id.visa_letter_enabled and self.state in ('open', 'done')
                    and self.sale_status != 'to_pay' and self.visa_letter_state in ('none', 'rejected'))

    def _send(self, xmlid, **ctx):
        template = self.env.ref(xmlid, raise_if_not_found=False)
        if template:
            for registration in self:
                template.sudo().with_context(**ctx).send_mail(
                    registration.id, email_layout_xmlid='mail.mail_notification_light')

    # ------------------------------------------------------------
    # Transfer and cancellation
    # ------------------------------------------------------------

    def _conference_transfer(self, name, email, phone=None):
        """Hand the ticket to someone else. The old ticket's barcode and the
        old booking link stop working; passport details are cleared."""
        self.ensure_one()
        if not self._conference_can_transfer():
            raise UserError(_("This ticket can no longer be transferred."))
        name = (name or '').strip()
        email = email_normalize(email or '')
        if not name or not email:
            raise UserError(_("Enter the new attendee's name and a valid email address."))
        if email == email_normalize(self.email or ''):
            raise UserError(_("The ticket is already in this person's name."))

        previous = {'name': self.name, 'email': self.email}
        self._send('conference_registration.mail_template_ticket_transferred_out',
                   previous_name=previous['name'], previous_email=previous['email'], new_name=name)
        vals = {
            'name': name,
            'email': email,
            'phone': (phone or '').strip() or False,
            'barcode': self._get_random_barcode(),
            'conference_token': uuid.uuid4().hex,
            'transfer_count': self.transfer_count + 1,
            'visa_letter_state': 'none',
            'visa_letter_reference': False,
            'visa_letter_date': False,
            'visa_reject_reason': False,
        }
        vals.update({f: False for f in VISA_FIELDS})
        self.sudo().write(vals)
        self.sudo().message_post(
            body=_("Ticket transferred from %(old)s (%(old_email)s) to %(new)s (%(new_email)s).",
                   old=previous['name'], old_email=previous['email'], new=name, new_email=email),
            subtype_xmlid='mail.mt_note')
        self._send('conference_registration.mail_template_ticket_transferred_in')
        return True

    def _conference_self_cancel(self):
        self.ensure_one()
        if not self._conference_can_cancel():
            raise UserError(_("This booking can no longer be cancelled online. Please contact the organisers."))
        paid = self._is_paid_booking()
        self.sudo().action_cancel()
        if paid:
            self.sudo().refund_requested = True
            self.sudo().activity_schedule(
                'mail.mail_activity_data_todo',
                user_id=(self.event_id.user_id or self.env.ref('base.user_admin')).id,
                summary=_("Refund requested"),
                note=_("%(name)s cancelled a paid ticket for %(event)s. Check the cancellation "
                       "policy and refund order %(order)s if it applies.",
                       name=self.name, event=self.event_id.name, order=self.sale_order_id.name or ''),
            )
        self._send('conference_registration.mail_template_booking_cancelled', refund_requested=paid)
        return True

    # ------------------------------------------------------------
    # Visa invitation letters
    # ------------------------------------------------------------

    def _conference_visa_request(self, vals):
        self.ensure_one()
        if not self._conference_can_request_visa():
            raise UserError(_("A visa letter cannot be requested for this booking."))
        clean = {
            'visa_full_name': (vals.get('visa_full_name') or '').strip(),
            'visa_passport_number': (vals.get('visa_passport_number') or '').strip().upper(),
            'visa_nationality_id': vals.get('visa_nationality_id') or False,
            'visa_date_of_birth': vals.get('visa_date_of_birth') or False,
            'visa_passport_expiry': vals.get('visa_passport_expiry') or False,
        }
        if not all(clean.values()):
            raise UserError(_("Please fill in every passport field."))
        expiry = fields.Date.to_date(clean['visa_passport_expiry'])
        event_end = fields.Date.to_date(self.event_id.date_end)
        if expiry and event_end and expiry < event_end:
            raise UserError(_("The passport expires before the event ends."))
        clean.update({'visa_letter_state': 'requested', 'visa_reject_reason': False})
        self.sudo().write(clean)
        if self.event_id.visa_letter_auto_issue:
            self.sudo().action_visa_issue()
        else:
            self.sudo().activity_schedule(
                'mail.mail_activity_data_todo',
                user_id=(self.event_id.user_id or self.env.ref('base.user_admin')).id,
                summary=_("Review visa letter request"),
            )
        return True

    def action_visa_issue(self):
        for registration in self:
            if registration.visa_letter_state not in ('requested', 'rejected', 'issued'):
                raise UserError(_("%s has not requested a visa letter.", registration.name))
            missing = [registration._fields[f].string for f in VISA_FIELDS if not registration[f]]
            if missing:
                raise UserError(_("Passport details are missing for %(name)s: %(fields)s",
                                  name=registration.name, fields=', '.join(missing)))
            if registration.state not in ('open', 'done') or registration.sale_status == 'to_pay':
                raise UserError(_("%s's booking is not confirmed and paid yet.", registration.name))
            vals = {'visa_letter_state': 'issued', 'visa_letter_date': fields.Date.context_today(registration),
                    'visa_reject_reason': False}
            if not registration.visa_letter_reference:
                vals['visa_letter_reference'] = self.env['ir.sequence'].sudo().next_by_code('conference.visa.letter')
            registration.write(vals)
            registration.activity_feedback(['mail.mail_activity_data_todo'])
            registration._send('conference_registration.mail_template_visa_issued')
        return True

    def action_visa_reject(self):
        for registration in self:
            if not (registration.visa_reject_reason or '').strip():
                raise UserError(_("Write the reason for %s first. It is sent to the attendee.", registration.name))
        self.write({'visa_letter_state': 'rejected'})
        self.activity_feedback(['mail.mail_activity_data_todo'])
        self._send('conference_registration.mail_template_visa_rejected')
        return True

    def _visa_verification_code(self):
        self.ensure_one()
        return tools.hmac(self.env(su=True), 'conference-visa-letter', (self.id, self.visa_letter_reference))[:12]

    def _get_visa_verify_url(self):
        self.ensure_one()
        return '%s/event/visa-letter/verify/%s/%s' % (
            self.get_base_url(), self.visa_letter_reference, self._visa_verification_code())

    def _visa_masked_passport(self):
        self.ensure_one()
        number = self.visa_passport_number or ''
        return ('•' * max(len(number) - 3, 0)) + number[-3:]

    def action_open_booking_page(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_url', 'url': self._get_manage_url(), 'target': 'new'}

    def action_send_manage_link(self):
        self._send('conference_registration.mail_template_manage_booking')
        return True

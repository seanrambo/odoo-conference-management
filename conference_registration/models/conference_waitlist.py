import uuid
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import email_normalize

WAITLIST_STATES = [
    ('waiting', 'Waiting'),
    ('offered', 'Seat Offered'),
    ('booked', 'Booked'),
    ('expired', 'Offer Expired'),
    ('cancelled', 'Left Waitlist'),
]


class ConferenceWaitlist(models.Model):
    _name = 'conference.waitlist'
    _description = 'Event Waitlist Entry'
    _inherit = ['mail.thread']
    _order = 'create_date, id'

    name = fields.Char('Name', required=True, tracking=True)
    email = fields.Char('Email', required=True, tracking=True)
    phone = fields.Char('Phone')
    partner_id = fields.Many2one('res.partner', 'Contact', index='btree_not_null')
    event_id = fields.Many2one('event.event', 'Event', required=True, index=True, ondelete='cascade')
    event_ticket_id = fields.Many2one(
        'event.event.ticket', 'Ticket', ondelete='set null', domain="[('event_id', '=', event_id)]")
    company_id = fields.Many2one(related='event_id.company_id', store=True)
    state = fields.Selection(WAITLIST_STATES, 'Status', default='waiting', required=True,
                             tracking=True, index=True, copy=False)
    position = fields.Integer('Position', compute='_compute_position')
    offer_date = fields.Datetime('Offered On', readonly=True, copy=False)
    offer_deadline = fields.Datetime('Offer Expires', readonly=True, copy=False)
    access_token = fields.Char(readonly=True, copy=False, index=True,
                               default=lambda self: uuid.uuid4().hex)
    sale_order_id = fields.Many2one('sale.order', 'Order', readonly=True, copy=False, index='btree_not_null')
    registration_id = fields.Many2one('event.registration', 'Registration', readonly=True, copy=False)

    @api.depends('state', 'event_id')
    def _compute_position(self):
        waiting = self.filtered(lambda w: w.state == 'waiting' and w.id)
        (self - waiting).position = 0
        for event in waiting.event_id:
            queue = self.sudo().search([('event_id', '=', event.id), ('state', '=', 'waiting')])
            ranks = {entry.id: i + 1 for i, entry in enumerate(queue)}
            for entry in waiting.filtered(lambda w: w.event_id == event):
                entry.position = ranks.get(entry.id, 0)

    @api.constrains('event_ticket_id', 'event_id')
    def _check_ticket_event(self):
        for entry in self:
            if entry.event_ticket_id and entry.event_ticket_id.event_id != entry.event_id:
                raise ValidationError(_("The ticket belongs to another event."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            email = email_normalize(vals.get('email') or '')
            if not email:
                raise UserError(_("Please enter a valid email address."))
            vals['email'] = email
            if self.search_count([('event_id', '=', vals.get('event_id')), ('email', '=', email),
                                  ('state', 'in', ('waiting', 'offered'))], limit=1):
                raise UserError(_("%s is already on the waitlist for this event.", email))
            if not vals.get('partner_id'):
                partner = self.env['res.partner'].sudo().search([('email', '=ilike', email)], limit=1)
                vals['partner_id'] = partner.id
        entries = super().create(vals_list)
        entries._send('conference_registration.mail_template_waitlist_joined')
        return entries

    # ------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------

    def _send(self, xmlid):
        template = self.env.ref(xmlid, raise_if_not_found=False)
        if template:
            for entry in self:
                template.sudo().send_mail(entry.id, email_layout_xmlid='mail.mail_notification_light')

    def _get_manage_url(self):
        self.ensure_one()
        return '%s/event/waitlist/%s' % (self.get_base_url(), self.access_token)

    def _is_free(self):
        self.ensure_one()
        ticket = self.event_ticket_id
        if not ticket:
            return not self.event_id.event_ticket_ids
        return not ticket.product_id or self.event_id.company_id.currency_id.is_zero(ticket.price)

    def _get_partner(self):
        self.ensure_one()
        if not self.partner_id:
            self.partner_id = self.env['res.partner'].sudo().create({
                'name': self.name, 'email': self.email, 'phone': self.phone})
        return self.partner_id

    def _registration_vals(self):
        self.ensure_one()
        return {
            'event_id': self.event_id.id,
            'event_ticket_id': self.event_ticket_id.id,
            'name': self.name,
            'email': self.email,
            'phone': self.phone,
            'partner_id': self._get_partner().id,
        }

    # ------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------

    def _offer(self):
        """Give the next freed seat to these entries. Free seats are booked
        straight away; paid seats are held until the offer deadline."""
        now = fields.Datetime.now()
        for entry in self.sudo():
            if entry.state != 'waiting':
                continue
            if entry._is_free():
                registration = self.env['event.registration'].sudo().create(entry._registration_vals())
                entry.write({'state': 'booked', 'registration_id': registration.id, 'offer_date': now})
                entry._send('conference_registration.mail_template_waitlist_booked')
            else:
                hours = entry.event_id.waitlist_offer_hours or 24
                entry.write({
                    'state': 'offered',
                    'offer_date': now,
                    'offer_deadline': now + timedelta(hours=hours),
                })
                entry._send('conference_registration.mail_template_waitlist_offer')

    def _get_or_create_order(self):
        """Quotation the waitlisted person pays online to take the held seat."""
        self.ensure_one()
        if self.state != 'offered':
            raise UserError(_("This seat is no longer on offer."))
        if self.offer_deadline and self.offer_deadline < fields.Datetime.now():
            raise UserError(_("This offer has expired."))
        order = self.sale_order_id
        if order and order.state in ('draft', 'sent'):
            return order
        ticket = self.event_ticket_id
        if not ticket or not ticket.product_id:
            raise UserError(_("This waitlist entry has no ticket to sell. Please contact the organisers."))
        partner = self._get_partner()
        order = self.env['sale.order'].sudo().create({
            'partner_id': partner.id,
            'company_id': self.event_id.company_id.id or self.env.company.id,
            'require_payment': True,
            # Paying is the acceptance: a signature would stop the order
            # from confirming automatically after online payment.
            'require_signature': False,
            'validity_date': fields.Date.to_date(self.offer_deadline) if self.offer_deadline else False,
            'order_line': [(0, 0, {
                'product_id': ticket.product_id.id,
                'event_id': self.event_id.id,
                'event_ticket_id': ticket.id,
                'product_uom_qty': 1,
            })],
        })
        self.sale_order_id = order
        return order

    def action_offer_now(self):
        """Organiser override: offer a seat to this person now."""
        for entry in self:
            if entry.state != 'waiting':
                raise UserError(_("Only waiting entries can be offered a seat."))
        self._offer()
        return True

    def action_cancel(self):
        for entry in self.sudo():
            if entry.state not in ('waiting', 'offered'):
                continue
            was_offered = entry.state == 'offered'
            if entry.sale_order_id.state in ('draft', 'sent'):
                entry.sale_order_id._action_cancel()
            entry.state = 'cancelled'
            if was_offered:
                entry.event_id._conference_waitlist_promote()
        return True

    def action_reset_waiting(self):
        self.filtered(lambda e: e.state in ('expired', 'cancelled')).write({
            'state': 'waiting', 'offer_date': False, 'offer_deadline': False})
        return True

    @api.model
    def _cron_process_waitlists(self):
        """Expire unanswered offers, then offer freed seats to the next people."""
        now = fields.Datetime.now()
        expired = self.sudo().search([('state', '=', 'offered'), ('offer_deadline', '<', now)])
        for entry in expired:
            if entry.sale_order_id.state in ('draft', 'sent'):
                entry.sale_order_id._action_cancel()
            entry.state = 'expired'
            entry._send('conference_registration.mail_template_waitlist_expired')
        events = self.sudo().search([('state', '=', 'waiting')]).event_id
        events._conference_waitlist_promote()

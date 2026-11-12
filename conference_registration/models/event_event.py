from odoo import _, api, fields, models


class EventEvent(models.Model):
    _inherit = 'event.event'

    # Waitlist
    waitlist_enabled = fields.Boolean(
        'Waitlist', default=True,
        help="When the event is sold out, visitors can join a waitlist. "
             "Freed seats are offered to them in order.")
    waitlist_offer_hours = fields.Integer(
        'Offer Valid For (hours)', default=24,
        help="How long a freed paid seat is held for the next person before it moves on.")
    waitlist_ids = fields.One2many('conference.waitlist', 'event_id', string='Waitlist Entries')
    waitlist_count = fields.Integer('Waiting', compute='_compute_waitlist_count')

    # Self-service
    transfer_enabled = fields.Boolean('Ticket Transfers', default=True)
    transfer_deadline_hours = fields.Integer(
        'Transfers Close (hours before start)', default=24)
    self_cancel_enabled = fields.Boolean('Self-service Cancellation', default=True)
    cancel_deadline_hours = fields.Integer(
        'Cancellations Close (hours before start)', default=72)
    cancel_policy = fields.Text(
        'Cancellation Policy', translate=True,
        help="Shown to attendees before they cancel, e.g. your refund terms.")

    # Visa letters
    visa_letter_enabled = fields.Boolean('Visa Invitation Letters')
    visa_letter_auto_issue = fields.Boolean(
        'Issue Automatically',
        help="Issue letters as soon as a confirmed attendee requests one. "
             "Otherwise an organiser reviews each request.")
    visa_signatory_name = fields.Char('Signatory Name')
    visa_signatory_title = fields.Char('Signatory Title')
    visa_signature = fields.Image('Signature', max_width=600, max_height=240)
    visa_letter_note = fields.Html(
        'Additional Paragraph', translate=True,
        help="Optional text added to every letter, for example who covers travel costs.")
    visa_request_count = fields.Integer('Visa Letters to Review', compute='_compute_visa_request_count')

    _waitlist_offer_hours_positive = models.Constraint(
        'CHECK(waitlist_offer_hours >= 1)', 'A waitlist offer must last at least one hour.')

    def _compute_waitlist_count(self):
        data = dict(self.env['conference.waitlist']._read_group(
            [('event_id', 'in', self.ids), ('state', 'in', ('waiting', 'offered'))], ['event_id'], ['__count']))
        for event in self:
            event.waitlist_count = data.get(event, 0)

    def _compute_visa_request_count(self):
        data = dict(self.env['event.registration']._read_group(
            [('event_id', 'in', self.ids), ('visa_letter_state', '=', 'requested')], ['event_id'], ['__count']))
        for event in self:
            event.visa_request_count = data.get(event, 0)

    # ------------------------------------------------------------
    # Seats held for waitlist offers
    # ------------------------------------------------------------

    def _conference_held_seats(self, by_ticket=False):
        """Seats promised to waitlisted people who have an open offer.
        Returns {event_id: count}, or {ticket_id: count} with ``by_ticket``."""
        ids = [i for i in self.ids if isinstance(i, int)]
        if not ids:
            return {}
        group = 'event_ticket_id' if by_ticket else 'event_id'
        data = self.env['conference.waitlist'].sudo()._read_group(
            [('event_id', 'in', ids), ('state', '=', 'offered')], [group], ['__count'])
        return {record.id: count for record, count in data if record}

    @api.depends('event_slot_count', 'is_multi_slots', 'seats_max', 'registration_ids.state',
                 'registration_ids.active', 'waitlist_ids.state')
    def _compute_seats(self):
        super()._compute_seats()
        held = self._conference_held_seats()
        for event in self:
            count = held.get(event._origin.id or event.id, 0)
            if count and event.seats_max > 0 and not event.is_multi_slots:
                event.seats_available -= count

    def _conference_waitlist_promote(self):
        """Offer every free seat to the next people on the waitlist."""
        now = fields.Datetime.now()
        for event in self.sudo():
            if not (event.waitlist_enabled and event.seats_limited and event.seats_max) or event.is_multi_slots:
                continue
            if event.date_end and event.date_end < now:
                continue
            for entry in event.waitlist_ids.filtered(lambda w: w.state == 'waiting').sorted(lambda w: (w.create_date, w.id)):
                if event.seats_available <= 0:
                    break
                ticket = entry.event_ticket_id
                if ticket and ticket.seats_limited and ticket.seats_max and ticket.seats_available <= 0:
                    continue
                entry._offer()

    def write(self, vals):
        res = super().write(vals)
        if {'seats_max', 'seats_limited', 'waitlist_enabled'} & set(vals):
            self._conference_waitlist_promote()
        return res

    # ------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------

    def action_view_waitlist(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('conference_registration.conference_waitlist_action')
        action['domain'] = [('event_id', '=', self.id)]
        action['context'] = {'default_event_id': self.id, 'search_default_active': 1}
        return action

    def action_view_visa_requests(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('conference_registration.visa_letter_action')
        action['domain'] = [('event_id', '=', self.id)]
        return action

    def action_open_group_booking(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Group Booking'),
            'res_model': 'conference.group.booking',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_event_id': self.id},
        }

from odoo import api, fields, models


class EventEventTicket(models.Model):
    _inherit = 'event.event.ticket'

    waitlist_ids = fields.One2many('conference.waitlist', 'event_ticket_id', string='Waitlist')

    @api.depends('seats_max', 'registration_ids.state', 'registration_ids.active', 'waitlist_ids.state')
    def _compute_seats(self):
        super()._compute_seats()
        held = self.event_id._conference_held_seats(by_ticket=True)
        for ticket in self:
            count = held.get(ticket._origin.id or ticket.id, 0)
            if count and ticket.seats_max > 0:
                ticket.seats_available -= count

    def write(self, vals):
        res = super().write(vals)
        if 'seats_max' in vals or 'seats_limited' in vals:
            self.event_id._conference_waitlist_promote()
        return res

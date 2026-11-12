from odoo import fields, models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    conference_waitlist_ids = fields.One2many('conference.waitlist', 'sale_order_id', string='Waitlist Offers')

    def action_confirm(self):
        # Release the held seats first, so the registrations created on
        # confirmation can take them.
        offers = self.sudo().conference_waitlist_ids.filtered(lambda w: w.state in ('offered', 'expired'))
        offers.write({'state': 'booked'})
        res = super().action_confirm()
        for offer in offers:
            registrations = offer.sale_order_id.order_line.registration_ids.filtered(
                lambda r: r.event_id == offer.event_id and r.state != 'cancel')[:1]
            if not registrations:
                continue
            registrations.sudo().write({
                'name': offer.name, 'email': offer.email, 'phone': offer.phone,
                'partner_id': offer.partner_id.id,
            })
            registrations.filtered(lambda r: r.state == 'draft').sudo().action_confirm()
            offer.registration_id = registrations
        return res

import csv
import io
from collections import OrderedDict

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import email_normalize


class ConferenceGroupBooking(models.TransientModel):
    _name = 'conference.group.booking'
    _description = 'Conference Group Booking'

    event_id = fields.Many2one('event.event', 'Event', required=True)
    partner_id = fields.Many2one(
        'res.partner', 'Billed To', required=True,
        help="The company or person who pays. Receives one order and one invoice.")
    default_ticket_id = fields.Many2one(
        'event.event.ticket', 'Default Ticket', domain="[('event_id', '=', event_id)]")
    attendees_text = fields.Text(
        'Paste Attendees',
        help="One attendee per line: Name, Email, Phone. Phone is optional. "
             "You can paste straight from a spreadsheet.")
    line_ids = fields.One2many('conference.group.booking.line', 'wizard_id', string='Attendees')
    confirm_order = fields.Boolean('Confirm Order', default=True,
                                   help="Confirm now to reserve the seats.")
    create_invoice = fields.Boolean('Create Invoice', default=True)
    attendee_count = fields.Integer(compute='_compute_attendee_count')

    @api.depends('line_ids')
    def _compute_attendee_count(self):
        for wizard in self:
            wizard.attendee_count = len(wizard.line_ids)

    @api.onchange('create_invoice')
    def _onchange_create_invoice(self):
        if self.create_invoice:
            self.confirm_order = True

    def _parse_attendees(self, text):
        rows = []
        sample = (text or '').strip()
        if not sample:
            return rows
        delimiter = '\t' if '\t' in sample else (';' if sample.count(';') > sample.count(',') else ',')
        for number, row in enumerate(csv.reader(io.StringIO(sample), delimiter=delimiter), start=1):
            cells = [c.strip() for c in row]
            if not any(cells):
                continue
            name = cells[0] if cells else ''
            email = cells[1] if len(cells) > 1 else ''
            if number == 1 and email.lower() in ('email', 'e-mail', 'mail'):
                continue  # header row
            if not name or not email_normalize(email):
                raise UserError(_("Line %(n)s needs a name and a valid email: %(line)s",
                                  n=number, line=', '.join(cells)))
            rows.append({'name': name, 'email': email_normalize(email),
                         'phone': cells[2] if len(cells) > 2 else False})
        return rows

    def action_load_attendees(self):
        self.ensure_one()
        rows = self._parse_attendees(self.attendees_text)
        if not rows:
            raise UserError(_("Paste at least one attendee first."))
        self.write({
            'line_ids': [(0, 0, dict(row, ticket_id=self.default_ticket_id.id)) for row in rows],
            'attendees_text': False,
        })
        return {
            'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id,
            'view_mode': 'form', 'target': 'new', 'name': _('Group Booking'),
        }

    def action_book(self):
        self.ensure_one()
        if self.attendees_text and not self.line_ids:
            self.action_load_attendees()
        lines = self.line_ids
        if not lines:
            raise UserError(_("Add at least one attendee."))
        emails = [l.email for l in lines]
        duplicates = {e for e in emails if emails.count(e) > 1}
        if duplicates:
            raise UserError(_("These emails appear more than once: %s", ', '.join(sorted(duplicates))))
        if self.event_id.event_ticket_ids and any(not l.ticket_id for l in lines):
            raise UserError(_("Choose a ticket for every attendee."))

        by_ticket = OrderedDict()
        for line in lines:
            by_ticket.setdefault(line.ticket_id, self.env['conference.group.booking.line'])
            by_ticket[line.ticket_id] |= line
        if any(not ticket.product_id for ticket in by_ticket if ticket):
            raise UserError(_("Every ticket needs a product to be sold. Check the event's tickets."))
        if not any(by_ticket):
            raise UserError(_("This event has no tickets to sell. Add a ticket first."))

        order = self.env['sale.order'].create({
            'partner_id': self.partner_id.id,
            'company_id': self.event_id.company_id.id or self.env.company.id,
            'order_line': [(0, 0, {
                'product_id': ticket.product_id.id,
                'event_id': self.event_id.id,
                'event_ticket_id': ticket.id,
                'product_uom_qty': len(ticket_lines),
            }) for ticket, ticket_lines in by_ticket.items()],
        })
        order.message_post(body=_("Group booking of %s attendees for %s.", len(lines), self.event_id.name))
        if not self.confirm_order:
            return self._open(order)

        order.action_confirm()
        for so_line in order.order_line:
            ticket_lines = by_ticket.get(so_line.event_ticket_id)
            registrations = so_line.registration_ids.sorted('id')
            for registration, line in zip(registrations, ticket_lines):
                registration.write({'name': line.name, 'email': line.email, 'phone': line.phone})
            registrations.filtered(lambda r: r.state == 'draft').action_confirm()

        if self.create_invoice:
            invoice = order._create_invoices()
            return self._open(invoice)
        return self._open(order)

    def _open(self, record):
        return {
            'type': 'ir.actions.act_window',
            'res_model': record._name,
            'res_id': record.id,
            'view_mode': 'form',
            'target': 'current',
        }


class ConferenceGroupBookingLine(models.TransientModel):
    _name = 'conference.group.booking.line'
    _description = 'Conference Group Booking Attendee'

    wizard_id = fields.Many2one('conference.group.booking', required=True, ondelete='cascade')
    name = fields.Char('Name', required=True)
    email = fields.Char('Email', required=True)
    phone = fields.Char('Phone')
    ticket_id = fields.Many2one('event.event.ticket', 'Ticket')

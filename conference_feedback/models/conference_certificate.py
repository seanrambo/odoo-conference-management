import logging
from urllib.parse import quote

from odoo import _, api, fields, models, tools
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class ConferenceCertificate(models.Model):
    _name = 'conference.certificate'
    _description = 'Certificate of Attendance'
    _inherit = ['mail.thread']
    _order = 'issue_date desc, id desc'
    _primary_email = 'attendee_email'

    name = fields.Char('Number', required=True, readonly=True, copy=False, default=lambda self: _('New'))
    registration_id = fields.Many2one('event.registration', 'Attendee', required=True, index=True, ondelete='cascade')
    event_id = fields.Many2one(related='registration_id.event_id', store=True, index=True)
    company_id = fields.Many2one(related='registration_id.event_id.company_id', store=True)
    attendee_name = fields.Char('Name on Certificate', required=True, tracking=True)
    attendee_email = fields.Char('Email')
    issue_date = fields.Date('Issued On', required=True, default=fields.Date.context_today, readonly=True)
    session_count = fields.Integer('Sessions Attended', readonly=True)
    cpd_points = fields.Float('CPD Points', digits=(16, 2), readonly=True)
    cpd_label = fields.Char(related='event_id.cpd_label')
    state = fields.Selection([('issued', 'Issued'), ('revoked', 'Revoked')], default='issued',
                             required=True, tracking=True)
    method = fields.Selection([('rules', 'Met the rules'), ('manual', 'Issued by an organiser')],
                              default='rules', required=True, readonly=True)

    _registration_uniq = models.Constraint('unique(registration_id)', 'An attendee has only one certificate per event.')

    @api.model_create_multi
    def create(self, vals_list):
        sequence = self.env['ir.sequence'].sudo()
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = sequence.next_by_code('conference.certificate') or _('New')
        return super().create(vals_list)

    def _verification_code(self):
        self.ensure_one()
        return tools.hmac(self.env(su=True), 'conference-certificate', (self.id, self.name))[:12]

    def _get_verify_url(self):
        self.ensure_one()
        return '%s/conference/certificate/%s/%s' % (self.get_base_url(), self.name, self._verification_code())

    def _get_download_url(self):
        self.ensure_one()
        return self._get_verify_url() + '/pdf'

    def _get_qr_src(self):
        """Local barcode route for the verification QR code (rendered by the report engine)."""
        self.ensure_one()
        return '/report/barcode/QR/%s?width=240&height=240' % quote(self._get_verify_url(), safe='')

    def _attended_tracks(self):
        self.ensure_one()
        return self.registration_id.onsite_attendance_ids.track_id.sorted(lambda t: (t.date or fields.Datetime.now(), t.id))

    def action_send(self):
        if self._send_certificates():
            raise UserError(_("The certificate could not be emailed. Check the chatter for the reason."))
        return True

    def _send_certificates(self):
        """Email each certificate with its PDF. A failure (for example no PDF engine on the
        server) is logged on the certificate and does not undo the others."""
        template = self.env.ref('conference_feedback.mail_template_certificate', raise_if_not_found=False)
        failed = self.browse()
        if not template:
            return failed
        for certificate in self.filtered(lambda c: c.state == 'issued' and c.attendee_email):
            try:
                with self.env.cr.savepoint():
                    template.sudo().send_mail(certificate.id, email_layout_xmlid='mail.mail_notification_light')
            except Exception as e:  # noqa: BLE001 - keep issuing the rest
                _logger.warning("Could not email certificate %s: %s", certificate.name, e)
                certificate.message_post(body=_("The certificate could not be emailed: %s", e))
                failed |= certificate
        return failed

    def action_revoke(self):
        self.write({'state': 'revoked'})
        return True

    def action_reinstate(self):
        self.write({'state': 'issued'})
        return True

    def action_print(self):
        return self.env.ref('conference_feedback.action_report_certificate').report_action(self)

    def action_open_verify_page(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_url', 'url': self._get_verify_url(), 'target': 'new'}

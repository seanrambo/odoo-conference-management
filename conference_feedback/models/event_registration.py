import logging
import uuid

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare

_logger = logging.getLogger(__name__)


class EventRegistration(models.Model):
    _inherit = 'event.registration'

    # Set on first use, not as a column default: a default would give every
    # existing registration the same token when the module is installed.
    feedback_token = fields.Char('Feedback Token', copy=False, index='btree_not_null', groups='base.group_system')
    feedback_last_request = fields.Datetime('Last Feedback Request', copy=False, readonly=True)
    session_feedback_ids = fields.One2many('conference.session.feedback', 'registration_id', string='Session Feedback')
    feedback_survey_input_id = fields.Many2one('survey.user_input', 'Event Survey Answer', copy=False, readonly=True)
    feedback_survey_state = fields.Selection(related='feedback_survey_input_id.state', string='Survey')
    certificate_ids = fields.One2many('conference.certificate', 'registration_id', string='Certificates')
    certificate_id = fields.Many2one('conference.certificate', 'Certificate', compute='_compute_certificate_id')

    @api.depends('certificate_ids')
    def _compute_certificate_id(self):
        for registration in self:
            registration.certificate_id = registration.certificate_ids[:1]

    # ------------------------------------------------------------
    # Feedback page
    # ------------------------------------------------------------

    def _feedback_ensure_token(self):
        for registration in self.sudo():
            if not registration.feedback_token:
                registration.feedback_token = uuid.uuid4().hex
        return True

    def _get_feedback_url(self):
        self.ensure_one()
        self._feedback_ensure_token()
        return '%s/conference/feedback/%s' % (self.get_base_url(), self.sudo().feedback_token)

    # ------------------------------------------------------------
    # Event survey
    # ------------------------------------------------------------

    def _feedback_send_survey(self):
        """Create a personal survey link for each registration that has none yet, and email it."""
        template = self.env.ref('conference_feedback.mail_template_event_survey', raise_if_not_found=False)
        sent = self.browse()
        for registration in self.sudo():
            survey = registration.event_id.feedback_survey_id
            if not survey or registration.feedback_survey_input_id or not registration.email:
                continue
            partner = registration.partner_id
            if partner and (partner.email_normalized or '') != (registration.email or '').strip().lower():
                partner = False  # the buyer booked for someone else: invite the attendee by email
            try:
                answer = survey._create_answer(
                    partner=partner, email=registration.email, check_attempts=False,
                    nickname=registration.name, conference_registration_id=registration.id)
            except UserError as e:
                _logger.warning("Could not create a survey link for registration %s: %s", registration.id, e)
                continue
            registration.feedback_survey_input_id = answer
            if template:
                template.send_mail(registration.id, email_layout_xmlid='mail.mail_notification_light')
            sent |= registration
        return sent

    # ------------------------------------------------------------
    # Certificates
    # ------------------------------------------------------------

    def _certificate_eligibility(self):
        """Return (eligible, reasons) against the event's certificate rules."""
        self.ensure_one()
        event = self.event_id
        reasons = []
        if self.state != 'done':
            reasons.append(_("not checked in to the event"))
        if event.certificate_min_sessions and self.onsite_session_count < event.certificate_min_sessions:
            reasons.append(_("attended %(count)s of the %(min)s sessions needed",
                             count=self.onsite_session_count, min=event.certificate_min_sessions))
        if event.certificate_min_cpd and float_compare(self.cpd_points_total, event.certificate_min_cpd, 2) < 0:
            reasons.append(_("earned %(points)s of the %(min)s %(label)s needed",
                             points=self.cpd_points_total, min=event.certificate_min_cpd, label=event.cpd_label or ''))
        if event.certificate_require_survey and self.feedback_survey_state != 'done':
            reasons.append(_("has not completed the event survey"))
        return not reasons, reasons

    def _certificate_issue(self, method='rules', send=True):
        """Issue (or re-send nothing for) certificates. Skips registrations that already have one."""
        vals_list = []
        for registration in self:
            if registration.certificate_ids:
                continue
            vals_list.append({
                'registration_id': registration.id,
                'attendee_name': registration.name or registration.partner_id.name or registration.email,
                'attendee_email': registration.email or registration.partner_id.email,
                'session_count': registration.onsite_session_count,
                'cpd_points': registration.cpd_points_total if registration.event_id.cpd_enabled else 0.0,
                'method': method,
            })
        certificates = self.env['conference.certificate'].sudo().create(vals_list)
        if send:
            certificates._send_certificates()
        return certificates

    def _certificate_issue_if_due(self):
        """Automatic issuing, once the event has ended."""
        due = self.filtered(lambda r: r.event_id.certificate_enabled and r.event_id.certificate_auto
                            and r.event_id.is_finished and not r.certificate_ids
                            and r._certificate_eligibility()[0])
        return due._certificate_issue()

    def action_issue_certificate(self):
        """Organiser override: issue whatever the rules say."""
        for registration in self:
            if registration.state == 'cancel':
                raise UserError(_("%s's registration is cancelled.", registration.name))
            if not registration.event_id.certificate_enabled:
                raise UserError(_("Turn on certificates for %s first (After the Event tab).", registration.event_id.name))
        certificates = self._certificate_issue(method='manual')
        if len(certificates) == 1:
            return {'type': 'ir.actions.act_window', 'res_model': 'conference.certificate',
                    'res_id': certificates.id, 'view_mode': 'form'}
        return True

    def action_view_certificate(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'conference.certificate',
                'res_id': self.certificate_id.id, 'view_mode': 'form'}

from odoo import api, models


class EventType(models.Model):
    _inherit = 'event.type'

    @api.model
    def _default_event_mail_type_ids(self):
        """New events also send attendees a link to manage their booking
        (download the ticket, transfer it, cancel, request a visa letter)."""
        mails = super()._default_event_mail_type_ids()
        template = self.env.ref('conference_registration.mail_template_manage_booking', raise_if_not_found=False)
        if template:
            mails.append((0, 0, {
                'interval_nbr': 0,
                'interval_unit': 'now',
                'interval_type': 'after_sub',
                'template_ref': 'mail.template, %i' % template.id,
            }))
        return mails

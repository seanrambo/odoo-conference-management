from odoo import fields, models


class SurveyUserInput(models.Model):
    _inherit = 'survey.user_input'

    conference_registration_id = fields.Many2one(
        'event.registration', 'Event Attendee', index='btree_not_null', ondelete='set null', readonly=True)

    def _mark_done(self):
        res = super()._mark_done()
        registrations = self.filtered(lambda i: not i.test_entry).conference_registration_id
        if registrations:
            registrations.sudo()._certificate_issue_if_due()
        return res

from odoo import api, fields, models


class EventRegistration(models.Model):
    _inherit = 'event.registration'

    onsite_attendance_ids = fields.One2many(
        'conference.session.attendance', 'registration_id', string='Sessions Attended')
    onsite_session_count = fields.Integer('Session Count', compute='_compute_onsite_session_stats', store=True)
    cpd_points_total = fields.Float(
        'CPD Points Earned', digits=(16, 2), compute='_compute_onsite_session_stats', store=True)

    @api.depends('onsite_attendance_ids', 'onsite_attendance_ids.cpd_points')
    def _compute_onsite_session_stats(self):
        for registration in self:
            registration.onsite_session_count = len(registration.onsite_attendance_ids)
            registration.cpd_points_total = sum(registration.onsite_attendance_ids.mapped('cpd_points'))

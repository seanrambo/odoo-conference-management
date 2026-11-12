from odoo import api, fields, models


class EventTrack(models.Model):
    _inherit = 'event.track'

    session_feedback_ids = fields.One2many('conference.session.feedback', 'track_id', string='Feedback')
    feedback_count = fields.Integer('Ratings', compute='_compute_feedback_stats')
    feedback_rating_avg = fields.Float('Average Rating', digits=(16, 1), compute='_compute_feedback_stats')

    @api.depends('session_feedback_ids.state', 'session_feedback_ids.rating')
    def _compute_feedback_stats(self):
        stats = {
            track.id: (count, avg) for track, count, avg in self.env['conference.session.feedback']._read_group(
                [('track_id', 'in', self.ids), ('state', '=', 'done')], ['track_id'], ['__count', 'rating:avg'])
        }
        for track in self:
            track.feedback_count, track.feedback_rating_avg = stats.get(track._origin.id, (0, 0.0))

    def action_view_feedback(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('conference_feedback.conference_session_feedback_action')
        action['domain'] = [('track_id', '=', self.id)]
        action['context'] = {'search_default_answered': 1}
        return action

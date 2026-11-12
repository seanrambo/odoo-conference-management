import logging
from collections import defaultdict
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

# Only sessions that ended recently are asked about, so installing the module
# (or switching feedback on) never emails people about old events.
FEEDBACK_LOOKBACK = timedelta(days=3)


class ConferenceSessionFeedback(models.Model):
    _name = 'conference.session.feedback'
    _description = 'Session Feedback'
    _order = 'answer_date desc, id desc'
    _rec_name = 'track_id'

    track_id = fields.Many2one('event.track', 'Session', required=True, index=True, ondelete='cascade')
    event_id = fields.Many2one(related='track_id.event_id', store=True, index=True)
    company_id = fields.Many2one(related='track_id.event_id.company_id', store=True)
    location_id = fields.Many2one(related='track_id.location_id', store=True, string='Room')
    speaker_id = fields.Many2one(related='track_id.partner_id', store=True, string='Speaker')
    registration_id = fields.Many2one('event.registration', 'Attendee', required=True, index=True, ondelete='cascade')
    state = fields.Selection([('pending', 'Waiting'), ('done', 'Answered')], default='pending', required=True)
    rating = fields.Integer('Rating', aggregator='avg', help="1 (poor) to 5 (excellent).")
    comment = fields.Text('Comment')
    request_sent = fields.Boolean('Request Sent', readonly=True)
    answer_date = fields.Datetime('Answered On', readonly=True)

    _track_registration_uniq = models.Constraint(
        'unique(track_id, registration_id)',
        'An attendee can only rate a session once.')

    @api.constrains('rating', 'state')
    def _check_rating(self):
        for feedback in self:
            if feedback.state == 'done' and not 1 <= feedback.rating <= 5:
                raise ValidationError(_("A rating goes from 1 to 5."))

    def _record_answer(self, rating, comment=None):
        """Save an attendee's answer. Returns True when something was saved."""
        self.ensure_one()
        try:
            rating = int(rating or 0)
        except (TypeError, ValueError):
            rating = 0
        if not 1 <= rating <= 5:
            return False
        self.write({
            'rating': rating,
            'comment': (comment or '').strip() or False,
            'state': 'done',
            'answer_date': fields.Datetime.now(),
        })
        return True

    # ------------------------------------------------------------
    # Scheduled job
    # ------------------------------------------------------------

    @api.model
    def _cron_request_feedback(self):
        self._create_pending_feedback()
        self._send_feedback_requests()

    @api.model
    def _create_pending_feedback(self, now=None):
        """Create a feedback request for every attendee of every session that ended
        (plus the event's delay) within the lookback window."""
        now = now or fields.Datetime.now()
        events = self.env['event.event'].search([
            ('feedback_session_enabled', '=', True),
            ('date_end', '>=', now - FEEDBACK_LOOKBACK),
        ])
        vals_list = []
        for event in events:
            cutoff = now - timedelta(minutes=event.feedback_session_delay or 0)
            attendances = self.env['conference.session.attendance'].search([
                ('event_id', '=', event.id),
                ('track_id.date_end', '<=', cutoff),
                ('track_id.date_end', '>=', now - FEEDBACK_LOOKBACK),
                ('track_id.stage_id.is_cancel', '=', False),
                ('registration_id.state', 'in', ('open', 'done')),
            ])
            if not attendances:
                continue
            existing = set(self.search([('event_id', '=', event.id)]).mapped(
                lambda f: (f.track_id.id, f.registration_id.id)))
            for attendance in attendances:
                key = (attendance.track_id.id, attendance.registration_id.id)
                if key not in existing:
                    existing.add(key)
                    vals_list.append({'track_id': key[0], 'registration_id': key[1]})
        return self.create(vals_list)

    @api.model
    def _send_feedback_requests(self, now=None):
        """Email each attendee once about all their sessions not asked about yet,
        at most once every 'feedback_email_gap_hours' per attendee."""
        now = now or fields.Datetime.now()
        to_ask = self.search([('request_sent', '=', False), ('state', '=', 'pending')])
        by_registration = defaultdict(lambda: self.browse())
        for feedback in to_ask:
            by_registration[feedback.registration_id] |= feedback
        template = self.env.ref('conference_feedback.mail_template_session_feedback', raise_if_not_found=False)
        sent = self.env['event.registration']
        for registration, feedbacks in by_registration.items():
            gap = timedelta(hours=registration.event_id.feedback_email_gap_hours or 0)
            last = registration.feedback_last_request
            if last and last > now - gap:
                continue
            if not registration.email:
                feedbacks.request_sent = True
                continue
            registration._feedback_ensure_token()
            if template:
                template.sudo().with_context(feedback_new_ids=feedbacks.ids).send_mail(
                    registration.id, email_layout_xmlid='mail.mail_notification_light')
            feedbacks.request_sent = True
            sent |= registration
        sent.feedback_last_request = now
        return sent

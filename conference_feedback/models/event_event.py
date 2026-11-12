from collections import defaultdict
from datetime import timedelta

import pytz

from odoo import _, api, fields, models
from odoo.tools import format_amount, format_datetime, formataddr

POST_EVENT_LOOKBACK = timedelta(days=30)


class EventEvent(models.Model):
    _inherit = 'event.event'

    # Session ratings
    feedback_session_enabled = fields.Boolean(
        'Ask for Session Ratings', default=True,
        help="After a session ends, email the people scanned into it a link to rate it.")
    feedback_session_delay = fields.Integer(
        'Ask After (minutes)', default=15, help="How long after a session ends its attendees are asked to rate it.")
    feedback_email_gap_hours = fields.Integer(
        'At Most One Email Every (hours)', default=4,
        help="Ratings requests are grouped: an attendee gets at most one email in this time, listing every "
             "session they have not been asked about yet.")
    # Event survey
    feedback_survey_id = fields.Many2one(
        'survey.survey', 'Event Survey', domain=[('survey_type', '!=', 'live_session')],
        help="Each attendee is emailed a personal link to this survey after the event ends.")
    feedback_survey_delay_hours = fields.Integer('Send After (hours)', default=2)
    feedback_survey_audience = fields.Selection(
        [('attended', 'Checked-in attendees'), ('all', 'Everyone registered')],
        string='Send To', default='attended', required=True)
    feedback_survey_sent_date = fields.Datetime('Survey Sent On', readonly=True, copy=False)
    # Certificates
    certificate_enabled = fields.Boolean('Certificates of Attendance')
    certificate_title = fields.Char('Certificate Title', default='Certificate of Attendance', translate=True)
    certificate_auto = fields.Boolean(
        'Issue Certificates Automatically', default=True,
        help="When the event ends, issue and email certificates to everyone who meets the rules. People who "
             "meet them later (for example by completing the survey) get theirs then.")
    certificate_min_sessions = fields.Integer('Minimum Sessions', help="0 means no minimum.")
    certificate_min_cpd = fields.Float('Minimum CPD Points', digits=(16, 2), help="0 means no minimum.")
    certificate_require_survey = fields.Boolean('Survey Must Be Completed')
    certificate_signatory_name = fields.Char('Signed By')
    certificate_signatory_title = fields.Char('Certificate Signatory Title')
    certificate_signature = fields.Image('Certificate Signature', max_width=600, max_height=200)
    # Counters
    feedback_count = fields.Integer('Ratings', compute='_compute_feedback_counts')
    feedback_rating_avg = fields.Float('Average Rating', digits=(16, 1), compute='_compute_feedback_counts')
    certificate_count = fields.Integer('Certificates', compute='_compute_feedback_counts')

    def _compute_feedback_counts(self):
        ratings = {
            event.id: (count, avg) for event, count, avg in self.env['conference.session.feedback']._read_group(
                [('event_id', 'in', self.ids), ('state', '=', 'done')], ['event_id'], ['__count', 'rating:avg'])
        }
        certificates = dict(self.env['conference.certificate']._read_group(
            [('event_id', 'in', self.ids), ('state', '=', 'issued')], ['event_id'], ['__count']))
        for event in self:
            event.feedback_count, event.feedback_rating_avg = ratings.get(event._origin.id, (0, 0.0))
            event.certificate_count = certificates.get(event._origin, 0)

    # ------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------

    def _notify(self, message, kind='success'):
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'message': message, 'type': kind, 'sticky': False}}

    def action_view_session_feedback(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('conference_feedback.conference_session_feedback_action')
        action['domain'] = [('event_id', '=', self.id)]
        action['context'] = {'search_default_answered': 1, 'search_default_group_by_track': 1}
        return action

    def action_view_certificates(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('conference_feedback.conference_certificate_action')
        action['domain'] = [('event_id', '=', self.id)]
        return action

    def action_open_event_report(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.client',
            'tag': 'conference_feedback.event_report',
            'name': _("Event Report: %s", self.name),
            'context': {'active_id': self.id, 'active_model': 'event.event'},
        }

    def action_issue_certificates(self):
        self.ensure_one()
        certificates, not_eligible = self._certificate_issue_eligible()
        message = _("%(count)s certificates issued and emailed.", count=len(certificates))
        if not_eligible:
            message += " " + _("%(count)s attendees don't meet the rules yet.", count=not_eligible)
        return self._notify(message, 'success' if certificates else 'warning')

    def action_send_feedback_survey(self):
        self.ensure_one()
        if not self.feedback_survey_id:
            return self._notify(_("Pick an event survey first (After the Event tab)."), 'warning')
        sent = self._feedback_send_event_survey()
        return self._notify(_("Survey link emailed to %(count)s attendees.", count=len(sent)),
                            'success' if sent else 'warning')

    def action_send_speaker_scorecards(self):
        self.ensure_one()
        count = self._feedback_send_speaker_scorecards()
        return self._notify(_("Scorecards emailed to %(count)s speakers.", count=count),
                            'success' if count else 'warning')

    # ------------------------------------------------------------
    # Business
    # ------------------------------------------------------------

    def _feedback_survey_audience_registrations(self):
        self.ensure_one()
        states = ('done',) if self.feedback_survey_audience == 'attended' else ('open', 'done')
        return self.env['event.registration'].search([('event_id', '=', self.id), ('state', 'in', states)])

    def _feedback_send_event_survey(self):
        self.ensure_one()
        sent = self._feedback_survey_audience_registrations()._feedback_send_survey()
        self.sudo().feedback_survey_sent_date = fields.Datetime.now()
        return sent

    def _certificate_issue_eligible(self):
        """Issue certificates to every attendee who meets the rules and has none. Returns
        (certificates, number of attendees still not eligible)."""
        self.ensure_one()
        registrations = self.env['event.registration'].search([
            ('event_id', '=', self.id), ('state', 'in', ('open', 'done')), ('certificate_ids', '=', False)])
        eligible = registrations.filtered(lambda r: r._certificate_eligibility()[0])
        return eligible._certificate_issue(), len(registrations - eligible)

    def _feedback_speaker_scorecards(self):
        """{speaker partner: [session rows]} for sessions with a speaker that has an email."""
        self.ensure_one()
        cards = defaultdict(list)
        tz = self.date_tz
        tracks = self.track_ids.filtered(lambda t: t.partner_id.email and not t.stage_id.is_cancel)
        for track in tracks.sorted(lambda t: (t.date or self.date_begin, t.id)):
            answered = track.session_feedback_ids.filtered(lambda f: f.state == 'done')
            cards[track.partner_id].append({
                'name': track.name,
                'when': format_datetime(self.env, track.date, tz=tz, dt_format='EEE d MMM, HH:mm') if track.date else '',
                'room': track.location_id.name or '',
                'attended': track.onsite_attendance_count,
                'capacity': track.onsite_seat_limit,
                'rating_count': len(answered),
                'rating_avg': round(sum(answered.mapped('rating')) / len(answered), 1) if answered else 0,
                'comments': [c for c in answered.sorted('answer_date').mapped('comment') if c],
            })
        return cards

    def _feedback_send_speaker_scorecards(self):
        self.ensure_one()
        template = self.env.ref('conference_feedback.mail_template_speaker_scorecard', raise_if_not_found=False)
        if not template:
            return 0
        cards = self._feedback_speaker_scorecards()
        for speaker, rows in cards.items():
            template.sudo().with_context(scorecard_rows=rows, speaker_name=speaker.name).send_mail(
                self.id, email_values={'email_to': formataddr((speaker.name, speaker.email)), 'recipient_ids': []},
                email_layout_xmlid='mail.mail_notification_light')
        return len(cards)

    @api.model
    def _cron_post_event(self):
        now = fields.Datetime.now()
        recent = [('date_end', '<=', now), ('date_end', '>=', now - POST_EVENT_LOOKBACK)]
        for event in self.search(recent + [('feedback_survey_id', '!=', False), ('feedback_survey_sent_date', '=', False)]):
            if event.date_end + timedelta(hours=event.feedback_survey_delay_hours or 0) <= now:
                event._feedback_send_event_survey()
        for event in self.search(recent + [('certificate_enabled', '=', True), ('certificate_auto', '=', True)]):
            event._certificate_issue_eligible()

    # ------------------------------------------------------------
    # Event report
    # ------------------------------------------------------------

    def get_conference_report_data(self):
        """Everything the Event Report screen shows, in one call."""
        self.ensure_one()
        tz = pytz.timezone(self.date_tz or 'UTC')
        Registration = self.env['event.registration']
        registrations = Registration.search([('event_id', '=', self.id), ('state', '!=', 'cancel')])
        confirmed = registrations.filtered(lambda r: r.state in ('open', 'done'))
        attended = confirmed.filtered(lambda r: r.state == 'done')
        cancelled_count = Registration.search_count([('event_id', '=', self.id), ('state', '=', 'cancel')])

        # Registrations over time: cumulative confirmed registrations per day (event time zone).
        per_day = defaultdict(int)
        for reg in confirmed:
            per_day[pytz.utc.localize(reg.create_date).astimezone(tz).date()] += 1
        timeline, running = [], 0
        if per_day:
            # Start at zero the day before the first registration, so even one day draws a line.
            day, last = min(per_day) - timedelta(days=1), max(per_day)
            while day <= last:
                running += per_day.get(day, 0)
                timeline.append({'date': day.isoformat(), 'count': per_day.get(day, 0), 'total': running})
                day += timedelta(days=1)

        # Revenue, only for users allowed to see sales.
        revenue = None
        if self.env.user.has_group('sales_team.group_sale_salesman'):
            currency = self.company_id.currency_id or self.env.company.currency_id
            rows = self.env['sale.order.line']._read_group(
                [('event_id', '=', self.id), ('state', '=', 'sale')],
                ['event_ticket_id'], ['price_total:sum', 'product_uom_qty:sum'])
            tickets = [{
                'name': ticket.name or _("No ticket"),
                'seats': int(qty),
                'amount': amount,
                'amount_display': format_amount(self.env, amount, currency),
            } for ticket, amount, qty in rows]
            tickets.sort(key=lambda t: -t['amount'])
            total = sum(t['amount'] for t in tickets)
            revenue = {'total': total, 'total_display': format_amount(self.env, total, currency), 'tickets': tickets}

        # Sessions and speakers.
        feedback = self.env['conference.session.feedback'].search([('event_id', '=', self.id)])
        answered = feedback.filtered(lambda f: f.state == 'done')
        sessions, speakers = [], {}
        for track in self.track_ids.filtered('date').sorted(lambda t: (t.date, t.id)):
            track_answers = answered.filtered(lambda f: f.track_id == track)
            avg = round(sum(track_answers.mapped('rating')) / len(track_answers), 1) if track_answers else None
            sessions.append({
                'id': track.id,
                'name': track.name,
                'speaker': track.partner_name or track.partner_id.name or '',
                'room': track.location_id.name or '',
                'when': format_datetime(self.env, track.date, tz=self.date_tz, dt_format='EEE HH:mm'),
                'attended': track.onsite_attendance_count,
                'capacity': track.onsite_seat_limit,
                'rating_avg': avg,
                'rating_count': len(track_answers),
                'cancelled': track.stage_id.is_cancel,
            })
            name = track.partner_name or track.partner_id.name
            if name and not track.stage_id.is_cancel:
                card = speakers.setdefault(name, {'name': name, 'sessions': 0, 'attended': 0, 'ratings': []})
                card['sessions'] += 1
                card['attended'] += track.onsite_attendance_count
                card['ratings'] += track_answers.mapped('rating')
        speaker_rows = []
        for card in speakers.values():
            ratings = card.pop('ratings')
            card['rating_count'] = len(ratings)
            card['rating_avg'] = round(sum(ratings) / len(ratings), 1) if ratings else None
            speaker_rows.append(card)
        speaker_rows.sort(key=lambda s: (-(s['rating_avg'] or 0), -s['attended'], s['name']))

        survey_inputs = confirmed.feedback_survey_input_id
        return {
            'event': {
                'id': self.id,
                'name': self.name,
                'dates': '%s – %s' % (
                    format_datetime(self.env, self.date_begin, tz=self.date_tz, dt_format='d MMM y'),
                    format_datetime(self.env, self.date_end, tz=self.date_tz, dt_format='d MMM y')),
                'cpd_enabled': self.cpd_enabled,
                'cpd_label': self.cpd_label or _('CPD points'),
            },
            'kpis': {
                'registered': len(confirmed),
                'attended': len(attended),
                'checkin_rate': round(100.0 * len(attended) / len(confirmed)) if confirmed else 0,
                'cancelled': cancelled_count,
                'unconfirmed': len(registrations) - len(confirmed),
                'sessions': len([s for s in sessions if not s['cancelled']]),
                'session_scans': sum(s['attended'] for s in sessions),
                'rating_avg': round(sum(answered.mapped('rating')) / len(answered), 1) if answered else None,
                'rating_count': len(answered),
                'feedback_requested': len(feedback),
                'feedback_rate': round(100.0 * len(answered) / len(feedback)) if feedback else 0,
                'survey_sent': len(survey_inputs),
                'survey_done': len(survey_inputs.filtered(lambda i: i.state == 'done')),
                'certificates': self.certificate_count,
                'cpd_total': round(sum(attended.mapped('cpd_points_total')), 2),
            },
            'timeline': timeline,
            'revenue': revenue,
            'sessions': sessions,
            'speakers': speaker_rows,
        }

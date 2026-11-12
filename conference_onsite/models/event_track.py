from datetime import timedelta

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.tools import format_datetime, formataddr


class EventTrack(models.Model):
    _inherit = 'event.track'

    cpd_points = fields.Float(
        'CPD Points', digits=(16, 2),
        help="Continuing professional development points an attendee earns for attending this session.")
    cpd_enabled = fields.Boolean(related='event_id.cpd_enabled')
    onsite_seat_limit = fields.Integer(
        'Room Capacity',
        help="Most attendees the session scanner lets in before warning that the room is full. 0 means no limit.")
    onsite_attendance_ids = fields.One2many('conference.session.attendance', 'track_id', string='Attendance')
    onsite_attendance_count = fields.Integer('Attended', compute='_compute_onsite_attendance_count')

    @api.depends('onsite_attendance_ids')
    def _compute_onsite_attendance_count(self):
        counts = dict(self.env['conference.session.attendance']._read_group(
            [('track_id', 'in', self.ids)], ['track_id'], ['__count']))
        for track in self:
            track.onsite_attendance_count = counts.get(track._origin, 0)

    def action_view_onsite_attendance(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('conference_onsite.conference_session_attendance_action')
        action['domain'] = [('track_id', '=', self.id)]
        action['context'] = {'default_track_id': self.id}
        return action

    # ------------------------------------------------------------
    # Scanner helpers
    # ------------------------------------------------------------

    def _onsite_in_scan_window(self, now=None):
        """Whether 'now' falls within the session time, widened by the event's scan margin."""
        self.ensure_one()
        if not self.date or not self.date_end:
            return True
        now = now or fields.Datetime.now()
        margin = timedelta(minutes=self.event_id.onsite_scan_margin or 0)
        return self.date - margin <= now <= self.date_end + margin

    def _onsite_scanner_values(self, now):
        self.ensure_one()
        tz = self.event_id.date_tz
        return {
            'id': self.id,
            'name': self.name,
            'location': self.location_id.name or '',
            'day': format_datetime(self.env, self.date, tz=tz, dt_format='EEE d MMM'),
            'start': format_datetime(self.env, self.date, tz=tz, dt_format='HH:mm'),
            'end': format_datetime(self.env, self.date_end, tz=tz, dt_format='HH:mm') if self.date_end else '',
            'date_raw': self.date,
            'is_live': bool(self.date_end) and self.date <= now < self.date_end,
            'is_cancelled': self.stage_id.is_cancel,
            'count': self.onsite_attendance_count,
            'seat_limit': self.onsite_seat_limit,
            'cpd_points': self.cpd_points,
        }

    # ------------------------------------------------------------
    # Session change notifications
    # ------------------------------------------------------------

    _ONSITE_WATCHED_FIELDS = ('date', 'date_end', 'duration', 'location_id', 'stage_id')

    def write(self, vals):
        if self.env.context.get('onsite_skip_change_notify') or not any(f in vals for f in self._ONSITE_WATCHED_FIELDS):
            return super().write(vals)
        before = {track.id: track._onsite_snapshot() for track in self}
        res = super().write(vals)
        for track in self:
            track._onsite_notify_change(before[track.id])
        return res

    def _onsite_snapshot(self):
        self.ensure_one()
        return {
            'date': self.date,
            'date_end': self.date_end,
            'location': self.location_id,
            'is_cancel': self.stage_id.is_cancel,
            'is_published': self.is_published,
        }

    def _onsite_describe_changes(self, before):
        """Return (cancelled, [change dicts]) between the 'before' snapshot and now."""
        self.ensure_one()
        tz = self.event_id.date_tz
        if self.stage_id.is_cancel and not before['is_cancel']:
            return True, []

        def when(start, end):
            if not start:
                return _("not scheduled")
            text = format_datetime(self.env, start, tz=tz, dt_format='EEEE d MMMM, HH:mm')
            if end:
                text += '–' + format_datetime(self.env, end, tz=tz, dt_format='HH:mm')
            return text

        def minute(value):
            return value.replace(second=0, microsecond=0) if value else value

        changes = []
        if (minute(before['date']), minute(before['date_end'])) != (minute(self.date), minute(self.date_end)):
            changes.append({
                'label': _("Time"),
                'old': when(before['date'], before['date_end']),
                'new': when(self.date, self.date_end),
            })
        if before['location'] != self.location_id:
            changes.append({
                'label': _("Room"),
                'old': before['location'].name or _("not set"),
                'new': self.location_id.name or _("not set"),
            })
        return False, changes

    def _onsite_should_notify(self, before):
        self.ensure_one()
        event = self.event_id
        # Use the published state from before the write: moving a session to a
        # cancelled stage unpublishes it in the same write.
        if not event.onsite_notify_changes or event.is_finished or not before['is_published']:
            return False
        if before['is_cancel']:
            return False
        # Only sessions that had not finished yet: nobody needs an email about the past.
        old_end = before['date_end'] or before['date']
        return bool(old_end) and old_end > fields.Datetime.now()

    def _onsite_change_recipients(self):
        """People affected by a change to this session: (name, email) pairs, one per address.

        - visitors who saved the session to their agenda (unless they turned its reminder off);
        - for key sessions ("wishlisted by default"), every confirmed attendee of the event;
        - the speaker.
        """
        self.ensure_one()
        recipients = {}
        opted_out = set()

        def add(name, email):
            email_norm = (email or '').strip().lower()
            if email_norm and email_norm not in opted_out and email_norm not in recipients:
                recipients[email_norm] = (name or '', email.strip())

        track_visitors = self.sudo().event_track_visitor_ids
        for tv in track_visitors.filtered('is_blacklisted'):
            if tv.partner_id.email_normalized:
                opted_out.add(tv.partner_id.email_normalized)
        for tv in track_visitors.filtered(lambda v: v.is_wishlisted and not v.is_blacklisted):
            add(tv.partner_id.name, tv.partner_id.email)
        if self.wishlisted_by_default:
            registrations = self.env['event.registration'].sudo().search([
                ('event_id', '=', self.event_id.id), ('state', 'in', ('open', 'done'))])
            for reg in registrations:
                add(reg.name or reg.partner_id.name, reg.email or reg.partner_id.email)
        add(self.partner_name or self.partner_id.name, self.partner_email or self.partner_id.email)
        return list(recipients.values())

    def _onsite_notify_change(self, before):
        self.ensure_one()
        if not self._onsite_should_notify(before):
            return
        cancelled, changes = self._onsite_describe_changes(before)
        if not cancelled and not changes:
            return
        recipients = self._onsite_change_recipients()
        if not recipients:
            return
        template = self.env.ref(
            'conference_onsite.mail_template_session_cancelled' if cancelled
            else 'conference_onsite.mail_template_session_changed', raise_if_not_found=False)
        if not template:
            return
        template = template.sudo().with_context(onsite_changes=changes, onsite_before_when=self._onsite_when(before))
        subject = template._render_field('subject', self.ids)[self.id]
        body = template._render_field('body_html', self.ids, compute_lang=True)[self.id]
        email_from = (template._render_field('email_from', self.ids)[self.id]
                      or self.event_id.organizer_id.email_formatted or self.env.company.email_formatted)
        self.env['mail.mail'].sudo().create([{
            'subject': subject,
            'body_html': body,
            'email_from': email_from,
            'email_to': formataddr((name, email)),
            'model': self._name,
            'res_id': self.id,
            'auto_delete': True,
        } for name, email in recipients])

        if cancelled:
            summary = _("Cancellation emailed to %(count)s people.", count=len(recipients))
        else:
            summary = _("Change emailed to %(count)s people: %(changes)s.", count=len(recipients),
                        changes=", ".join(f"{c['label']} {c['old']} → {c['new']}" for c in changes))
        self.sudo().message_post(body=Markup("<p>%s</p>") % summary, subtype_xmlid='mail.mt_note')

    def _onsite_when(self, before):
        tz = self.event_id.date_tz
        if not before['date']:
            return ''
        return format_datetime(self.env, before['date'], tz=tz, dt_format='EEEE d MMMM, HH:mm')

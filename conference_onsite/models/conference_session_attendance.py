from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import format_datetime


class ConferenceSessionAttendance(models.Model):
    _name = 'conference.session.attendance'
    _description = 'Session Attendance'
    _order = 'scan_date desc, id desc'
    _rec_name = 'registration_id'

    track_id = fields.Many2one('event.track', 'Session', required=True, index=True, ondelete='cascade')
    event_id = fields.Many2one(related='track_id.event_id', store=True, index=True)
    company_id = fields.Many2one(related='track_id.event_id.company_id', store=True)
    location_id = fields.Many2one(related='track_id.location_id', store=True, string='Room')
    registration_id = fields.Many2one(
        'event.registration', 'Attendee', required=True, index=True, ondelete='cascade')
    partner_id = fields.Many2one(related='registration_id.partner_id', store=True)
    email = fields.Char(related='registration_id.email')
    scan_date = fields.Datetime('Scanned At', required=True, default=fields.Datetime.now, readonly=True)
    method = fields.Selection(
        [('scan', 'Badge Scan'), ('manual', 'Manual'), ('import', 'Import')],
        string='Method', required=True, default='manual')
    user_id = fields.Many2one('res.users', 'Recorded By', default=lambda self: self.env.user, readonly=True)
    cpd_points = fields.Float(
        'CPD Points', digits=(16, 2),
        help="Points earned for this session, copied from the session when the attendee is recorded.")
    override_reason = fields.Selection(
        [('outside_window', 'Outside Scan Window'), ('session_full', 'Room Full')],
        string='Override', readonly=True,
        help="Set when staff recorded the attendee despite a warning.")

    _track_registration_uniq = models.Constraint(
        'unique(track_id, registration_id)',
        'This attendee is already recorded for this session.')

    @api.constrains('track_id', 'registration_id')
    def _check_same_event(self):
        for attendance in self:
            if attendance.track_id.event_id != attendance.registration_id.event_id:
                raise UserError(_("%(attendee)s is registered for %(event)s, not for this session's event.",
                                  attendee=attendance.registration_id.display_name,
                                  event=attendance.registration_id.event_id.display_name))

    @api.model_create_multi
    def create(self, vals_list):
        tracks = self.env['event.track'].browse([v['track_id'] for v in vals_list if v.get('track_id')])
        points = {t.id: t.cpd_points for t in tracks}
        for vals in vals_list:
            if 'cpd_points' not in vals and vals.get('track_id'):
                vals['cpd_points'] = points.get(vals['track_id'], 0.0)
        return super().create(vals_list)

    # ------------------------------------------------------------
    # Scanner API (called from the session scanner screen)
    # ------------------------------------------------------------

    @api.model
    def scanner_events(self):
        """Current and upcoming events with scheduled sessions, for the scanner's event picker."""
        now = fields.Datetime.now()
        events = self.env['event.event'].search([
            ('date_end', '>=', now - timedelta(hours=12)),
            ('track_ids.date', '!=', False),
        ], order='date_begin, id', limit=30)
        return [{
            'id': event.id,
            'name': event.name,
            'dates': format_datetime(self.env, event.date_begin, tz=event.date_tz, dt_format='d MMM y'),
        } for event in events]

    @api.model
    def scanner_init(self, event_id):
        """Return the event and its scheduled sessions for the scanner screen."""
        event = self.env['event.event'].browse(event_id).exists()
        if not event:
            raise UserError(_("This event no longer exists."))
        now = fields.Datetime.now()
        tracks = event.track_ids.filtered('date').sorted(lambda t: (t.date, t.location_id.name or '', t.id))
        sessions = [track._onsite_scanner_values(now) for track in tracks]
        live = [s for s in sessions if s['is_live'] and not s['is_cancelled']]
        upcoming = [s for s in sessions if s['date_raw'] >= now and not s['is_cancelled']]
        default = (live or upcoming or sessions or [{}])[0].get('id', False)
        for s in sessions:
            s.pop('date_raw')
        return {
            'event_name': event.name,
            'checkin_count': event.onsite_checkin_count,
            'expected_count': event.onsite_expected_count,
            'sessions': sessions,
            'default_session_id': default,
        }

    @api.model
    def scanner_search(self, event_id, term):
        """Find confirmed attendees by name, email or company, for staff without a badge to scan."""
        term = (term or '').strip()
        if len(term) < 2:
            return []
        registrations = self.env['event.registration'].search([
            ('event_id', '=', event_id),
            ('state', 'in', ('open', 'done')),
            '|', '|', ('name', 'ilike', term), ('email', 'ilike', term), ('company_name', 'ilike', term),
        ], limit=10, order='name')
        return [{
            'id': reg.id,
            'name': reg.name or reg.display_name,
            'email': reg.email or '',
            'company': reg.company_name or '',
            'ticket': reg.event_ticket_id.name or '',
        } for reg in registrations]

    @api.model
    def scan(self, track_id, barcode=None, registration_id=None, force=False):
        """Record an attendee in a session.

        Pass a badge ``barcode`` (method "scan") or a ``registration_id``
        (method "manual"). Warnings that staff may override (scan window,
        room full) are only bypassed when ``force`` is set.

        Returns a dict with ``status`` and the details the screen shows.
        """
        track = self.env['event.track'].browse(track_id).exists()
        if not track:
            return self._scan_result('invalid_session', _("This session no longer exists."))
        Registration = self.env['event.registration']
        if barcode:
            registration = Registration.search([('barcode', '=', barcode.strip())], limit=1)
            method = 'scan'
        else:
            registration = Registration.browse(registration_id).exists() if registration_id else Registration
            method = 'manual'
        if not registration:
            return self._scan_result('invalid_ticket', _("This badge is not valid. Send the attendee to the registration desk."), track=track)

        if registration.event_id != track.event_id:
            return self._scan_result('wrong_event', _("This ticket is for %(event)s.", event=registration.event_id.name),
                                     track=track, registration=registration)
        if registration.state == 'cancel':
            return self._scan_result('cancelled_registration', _("This registration was cancelled."),
                                     track=track, registration=registration)
        if registration.state == 'draft':
            return self._scan_result('unconfirmed_registration', _("This registration is not confirmed yet. Send the attendee to the registration desk."),
                                     track=track, registration=registration)
        if track.stage_id.is_cancel:
            return self._scan_result('session_cancelled', _("This session has been cancelled."),
                                     track=track, registration=registration)

        existing = self.search([('track_id', '=', track.id), ('registration_id', '=', registration.id)], limit=1)
        if existing:
            return self._scan_result(
                'already_recorded',
                _("Already recorded at %(time)s.", time=format_datetime(
                    self.env, existing.scan_date, tz=track.event_id.date_tz, dt_format='HH:mm')),
                track=track, registration=registration)

        override = False
        if not track._onsite_in_scan_window():
            if not force:
                return self._scan_result(
                    'outside_window',
                    _("This session runs %(start)s to %(end)s. Record the attendee anyway?",
                      start=format_datetime(self.env, track.date, tz=track.event_id.date_tz, dt_format='EEE HH:mm'),
                      end=format_datetime(self.env, track.date_end, tz=track.event_id.date_tz, dt_format='HH:mm')),
                    track=track, registration=registration, can_force=True)
            override = 'outside_window'
        if track.onsite_seat_limit and track.onsite_attendance_count >= track.onsite_seat_limit:
            if not force:
                return self._scan_result(
                    'session_full',
                    _("The room is full (%(limit)s seats). Let the attendee in anyway?", limit=track.onsite_seat_limit),
                    track=track, registration=registration, can_force=True)
            override = 'session_full'

        self.create({
            'track_id': track.id,
            'registration_id': registration.id,
            'method': method,
            'override_reason': override,
        })
        checked_in_now = False
        if registration.state == 'open' and track.event_id.onsite_session_checkin:
            registration.action_set_done()
            checked_in_now = True
        message = _("Welcome to %(session)s.", session=track.name)
        if checked_in_now:
            message += " " + _("Also checked in to the event.")
        return self._scan_result('recorded', message, track=track, registration=registration)

    @api.model
    def _scan_result(self, status, message, track=None, registration=None, can_force=False):
        result = {
            'status': status,
            'message': message,
            'can_force': can_force,
            'success': status == 'recorded',
        }
        if registration:
            result['attendee'] = {
                'id': registration.id,
                'name': registration.name or registration.display_name,
                'company': registration.company_name or '',
                'ticket': registration.event_ticket_id.name or '',
            }
        if track:
            track.invalidate_recordset(['onsite_attendance_count'])
            result['session'] = {
                'id': track.id,
                'count': track.onsite_attendance_count,
                'seat_limit': track.onsite_seat_limit,
            }
        return result

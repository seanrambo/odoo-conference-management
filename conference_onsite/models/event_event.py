from odoo import _, api, fields, models


class EventEvent(models.Model):
    _inherit = 'event.event'

    onsite_scan_margin = fields.Integer(
        'Scan Window (minutes)', default=30,
        help="How long before a session starts, and after it ends, badges can be scanned into it "
             "without a warning.")
    onsite_session_checkin = fields.Boolean(
        'Check In on Session Scan', default=True,
        help="Scanning a badge at a session door also checks the attendee in to the event "
             "if they skipped the registration desk.")
    onsite_notify_changes = fields.Boolean(
        'Email Session Changes', default=True,
        help="When a published session's time or room changes, or it is cancelled, email the people who "
             "saved it to their agenda and its speaker. For key sessions, every attendee is emailed.")
    cpd_enabled = fields.Boolean('Award CPD Points', help="Award continuing professional development points per session attended.")
    cpd_label = fields.Char('Points Name', default='CPD points', translate=True,
                            help="What your accrediting body calls the points, e.g. CPD points or CME credits.")

    onsite_checkin_count = fields.Integer('Checked In', compute='_compute_onsite_counts')
    onsite_expected_count = fields.Integer('Expected', compute='_compute_onsite_counts')
    onsite_checkin_rate = fields.Float('Check-in Rate', compute='_compute_onsite_counts')
    onsite_session_attendance_count = fields.Integer('Session Scans', compute='_compute_onsite_counts')

    @api.depends('registration_ids.state')
    def _compute_onsite_counts(self):
        Registration = self.env['event.registration']
        states = dict.fromkeys(self.ids)
        for event, state, count in Registration._read_group(
                [('event_id', 'in', self.ids), ('state', 'in', ('open', 'done'))],
                ['event_id', 'state'], ['__count']):
            states[event.id] = dict(states[event.id] or {}, **{state: count})
        scans = dict(self.env['conference.session.attendance']._read_group(
            [('event_id', 'in', self.ids)], ['event_id'], ['__count']))
        for event in self:
            by_state = states.get(event._origin.id) or {}
            done = by_state.get('done', 0)
            expected = done + by_state.get('open', 0)
            event.onsite_checkin_count = done
            event.onsite_expected_count = expected
            event.onsite_checkin_rate = (100.0 * done / expected) if expected else 0.0
            event.onsite_session_attendance_count = scans.get(event._origin, 0)

    def action_open_session_scanner(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.client',
            'tag': 'conference_onsite.session_scanner',
            'name': _("Session Scanner"),
            'target': 'fullscreen',
            'context': {'default_event_id': self.id},
        }

    def action_view_onsite_checkins(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('event.event_registration_action_tree')
        action['domain'] = [('event_id', '=', self.id), ('state', 'in', ('open', 'done'))]
        action['context'] = {'default_event_id': self.id}
        return action

    def action_view_onsite_attendance(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('conference_onsite.conference_session_attendance_action')
        action['domain'] = [('event_id', '=', self.id)]
        action['context'] = {'search_default_group_by_track': 1}
        return action

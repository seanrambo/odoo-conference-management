import base64
import io
import os
import re
import zipfile

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import UserError


def _safe(text, max_len=40):
    """Make a string safe for use in a file name inside the ZIP."""
    text = re.sub(r'[^\w\-]+', '-', (text or '').strip(), flags=re.UNICODE).strip('-')
    return text[:max_len] or 'untitled'


class ConferenceAvPack(models.TransientModel):
    _name = 'conference.av.pack'
    _description = 'Conference AV Presentation Pack'

    event_id = fields.Many2one('event.event', 'Event', required=True)
    date = fields.Date('Day', help="Leave empty to include every day of the event.")
    location_ids = fields.Many2many(
        'event.track.location', string='Rooms',
        help="Leave empty to include every room.")
    document_scope = fields.Selection([
        ('slides', 'Slides only'),
        ('all', 'All presentation files'),
    ], 'Include', default='slides', required=True)
    only_approved = fields.Boolean('Approved files only', default=True)
    include_unscheduled = fields.Boolean(
        'Include unscheduled files',
        help="Also include files not yet linked to a scheduled session.")
    document_count = fields.Integer(compute='_compute_document_count')

    @api.depends('event_id', 'date', 'location_ids', 'document_scope', 'only_approved', 'include_unscheduled')
    def _compute_document_count(self):
        for wizard in self:
            wizard.document_count = len(wizard._get_documents()) if wizard.event_id else 0

    def _event_tz(self):
        return pytz.timezone(self.event_id.date_tz or 'UTC')

    def _local(self, dt):
        return pytz.utc.localize(dt).astimezone(self._event_tz())

    def _get_documents(self):
        self.ensure_one()
        domain = [('event_id', '=', self.event_id.id)]
        if self.document_scope == 'slides':
            domain.append(('document_type', '=', 'slides'))
        else:
            domain.append(('document_type', 'in', ('slides', 'paper', 'poster', 'handout', 'other')))
        if self.only_approved:
            domain.append(('state', '=', 'approved'))
        else:
            domain.append(('state', '!=', 'rejected'))
        if not self.include_unscheduled:
            domain.append(('date_start', '!=', False))
        if self.location_ids:
            domain.append(('location_id', 'in', self.location_ids.ids))
        docs = self.env['conference.document'].search(domain)
        if self.date:
            docs = docs.filtered(lambda d: d.date_start and self._local(d.date_start).date() == self.date)
        return docs.sorted(lambda d: (d.location_id.name or '', d.date_start or fields.Datetime.now(), d.id))

    def _zip_path(self, doc):
        ext = os.path.splitext(doc.filename or '')[1].lower()
        room = _safe(doc.location_id.name or _('No-room'), 30)
        speaker = _safe(doc.track_id.partner_name or '', 30)
        title = _safe(doc.track_id.name or doc.name, 40)
        if doc.date_start:
            local = self._local(doc.date_start)
            folder = '%s/%s' % (room, local.strftime('%Y-%m-%d'))
            base = '%s_%s_%s_%s' % (local.strftime('%H%M'), room, speaker, title)
        else:
            folder = _('Unscheduled')
            base = '%s_%s' % (_safe(doc.speaker_name or '', 30), _safe(doc.name, 40))
        if doc.version > 1:
            base += '_v%s' % doc.version
        return '%s/%s%s' % (folder, base, ext)

    def _build_zip(self):
        """Return (zip bytes, file name)."""
        self.ensure_one()
        docs = self._get_documents()
        if not docs:
            raise UserError(_("No files match these filters."))
        buffer = io.BytesIO()
        used = set()
        with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for doc in docs:
                path = self._zip_path(doc)
                stem, ext = os.path.splitext(path)
                n = 2
                while path in used:
                    path = '%s_%s%s' % (stem, n, ext)
                    n += 1
                used.add(path)
                archive.writestr(path, base64.b64decode(doc.file))
        parts = [_safe(self.event_id.name, 40), 'AV-pack']
        if self.date:
            parts.append(self.date.isoformat())
        if len(self.location_ids) == 1:
            parts.append(_safe(self.location_ids.name, 30))
        return buffer.getvalue(), '%s.zip' % '_'.join(parts)

    def action_download(self):
        self.ensure_one()
        if not self._get_documents():
            raise UserError(_("No files match these filters."))
        return {
            'type': 'ir.actions.act_url',
            'url': '/conference/av_pack/%s' % self.id,
            'target': 'download',
        }

import base64
import binascii
import os

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools.mimetypes import guess_mimetype

DOCUMENT_TYPES = [
    ('slides', 'Presentation Slides'),
    ('paper', 'Paper'),
    ('poster', 'Poster'),
    ('handout', 'Handout'),
    ('bio', 'Speaker Bio'),
    ('photo', 'Speaker Photo'),
    ('sponsor', 'Sponsor Material'),
    ('other', 'Other'),
]
VISIBILITY = [
    ('organizers', 'Organizers Only'),
    ('speakers', 'Speakers'),
    ('attendees', 'Registered Attendees'),
    ('public', 'Public'),
]


class ConferenceDocument(models.Model):
    _name = 'conference.document'
    _description = 'Conference Document'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'event_id, date_start, id desc'

    name = fields.Char('Title', required=True, tracking=True)
    event_id = fields.Many2one(
        'event.event', 'Event', required=True, index=True, ondelete='cascade', tracking=True)
    company_id = fields.Many2one(related='event_id.company_id', store=True)
    document_type = fields.Selection(
        DOCUMENT_TYPES, 'Type', required=True, default='slides', tracking=True)
    description = fields.Text('Description')
    file = fields.Binary('File', attachment=True, required=True)
    filename = fields.Char('File Name')
    file_size = fields.Integer('Size (bytes)', readonly=True)
    file_size_display = fields.Char('Size', compute='_compute_file_size_display')
    mimetype = fields.Char('MIME Type', readonly=True)
    version = fields.Integer('Version', default=1, readonly=True, copy=False)

    partner_id = fields.Many2one(
        'res.partner', 'Owner', index=True, tracking=True,
        groups='event.group_event_registration_desk,base.group_portal',
        help="Speaker, author or attendee the file belongs to.")
    submission_id = fields.Many2one(
        'conference.submission', 'Submission', index=True, ondelete='set null',
        domain="[('event_id', '=', event_id)]")
    track_id = fields.Many2one(
        'event.track', 'Session', index=True, ondelete='set null',
        domain="[('event_id', '=', event_id)]")
    registration_id = fields.Many2one(
        'event.registration', 'Attendee', index=True, ondelete='set null',
        domain="[('event_id', '=', event_id)]")
    location_id = fields.Many2one(related='track_id.location_id', string='Room', store=True)
    date_start = fields.Datetime(related='track_id.date', string='Session Start', store=True)
    speaker_name = fields.Char(related='track_id.partner_name', string='Speaker')

    visibility = fields.Selection(VISIBILITY, 'Visible To', required=True, default='attendees', tracking=True)
    state = fields.Selection([
        ('pending', 'Pending Approval'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ], 'Status', default='pending', required=True, tracking=True, copy=False, index=True)
    rejection_reason = fields.Text('Rejection Reason', copy=False)
    uploaded_via_portal = fields.Boolean(readonly=True, copy=False)

    _file_size_positive = models.Constraint('CHECK(file_size >= 0)', 'File size cannot be negative.')

    @api.depends('file_size')
    def _compute_file_size_display(self):
        for doc in self:
            size = doc.file_size or 0
            if size >= 1024 * 1024:
                doc.file_size_display = '%.1f MB' % (size / 1024 / 1024)
            elif size >= 1024:
                doc.file_size_display = '%.0f KB' % (size / 1024)
            else:
                doc.file_size_display = '%d B' % size

    @api.onchange('track_id')
    def _onchange_track_id(self):
        if self.track_id and not self.partner_id:
            self.partner_id = self.track_id.partner_id

    @api.onchange('submission_id')
    def _onchange_submission_id(self):
        if self.submission_id:
            self.partner_id = self.partner_id or self.submission_id.partner_id
            self.track_id = self.track_id or self.submission_id.track_id

    # ------------------------------------------------------------
    # File handling
    # ------------------------------------------------------------

    @api.model
    def _prepare_file_vals(self, vals):
        """Fill size and mimetype from the uploaded payload."""
        if vals.get('file'):
            try:
                raw = base64.b64decode(vals['file'], validate=False)
            except (binascii.Error, ValueError):
                raise UserError(_("The uploaded file could not be read."))
            vals['file_size'] = len(raw)
            vals['mimetype'] = guess_mimetype(raw)
        return vals

    @api.constrains('filename', 'file_size', 'event_id')
    def _check_file_rules(self):
        for doc in self:
            event = doc.event_id
            if not doc.filename:
                continue
            allowed = event._conference_allowed_extensions()
            ext = os.path.splitext(doc.filename)[1].lower().lstrip('.')
            if allowed and ext not in allowed:
                raise ValidationError(_(
                    "Files of type .%(ext)s are not accepted for %(event)s. Allowed: %(allowed)s",
                    ext=ext or '?', event=event.name, allowed=', '.join(sorted(allowed))))
            if event.doc_max_size_mb and doc.file_size > event.doc_max_size_mb * 1024 * 1024:
                raise ValidationError(_(
                    "%(file)s is larger than the %(max)s MB limit for %(event)s.",
                    file=doc.filename, max=event.doc_max_size_mb, event=event.name))

    @api.constrains('event_id', 'submission_id', 'track_id', 'registration_id')
    def _check_same_event(self):
        for doc in self:
            for linked in (doc.submission_id, doc.track_id, doc.registration_id):
                if linked and linked.sudo().event_id != doc.event_id:
                    raise ValidationError(_("%s belongs to another event.", linked.display_name))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._prepare_file_vals(vals)
            if not vals.get('name') and vals.get('filename'):
                vals['name'] = os.path.splitext(vals['filename'])[0]
        return super().create(vals_list)

    def write(self, vals):
        if vals.get('file'):
            self._prepare_file_vals(vals)
            self._archive_current_version()
            new_vals = dict(vals)
            for doc in self:
                per_doc = dict(new_vals, version=doc.version + 1)
                # A replaced file needs a fresh approval unless the organizer
                # sets the state explicitly in the same write.
                if 'state' not in vals and not self.env.user.has_group('event.group_event_user'):
                    per_doc['state'] = 'pending'
                super(ConferenceDocument, doc).write(per_doc)
            return True
        return super().write(vals)

    def _archive_current_version(self):
        """Keep the file being replaced as a chatter attachment so every
        earlier version stays downloadable from the document."""
        for doc in self.sudo():
            if not doc.file:
                continue
            old_name = doc.filename or doc.name
            attachment = self.env['ir.attachment'].sudo().create({
                'name': 'v%s - %s' % (doc.version, old_name),
                'datas': doc.file,
                'res_model': doc._name,
                'res_id': doc.id,
            })
            doc.message_post(
                body=_("Version %(version)s replaced (%(name)s).", version=doc.version, name=old_name),
                attachment_ids=attachment.ids,
                subtype_xmlid='mail.mt_note',
            )

    # ------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------

    def action_approve(self):
        self.write({'state': 'approved', 'rejection_reason': False})
        return True

    def action_reject(self):
        for doc in self:
            if not (doc.rejection_reason or '').strip():
                raise UserError(_("Write a rejection reason for %s first, so the owner knows what to fix.", doc.name))
        self.write({'state': 'rejected'})
        template = self.env.ref('conference_management.mail_template_document_rejected', raise_if_not_found=False)
        if template:
            for doc in self.sudo().filtered('partner_id'):
                doc.message_post_with_source(
                    template, subtype_xmlid='mail.mt_comment',
                    email_layout_xmlid='mail.mail_notification_light')
        return True

    def action_reset_pending(self):
        self.write({'state': 'pending'})
        return True

    def _get_download_url(self):
        self.ensure_one()
        return '/conference/document/%s/download' % self.id

    # ------------------------------------------------------------
    # Access (portal / website)
    # ------------------------------------------------------------

    def _can_be_downloaded_by(self, user):
        """Whether ``user`` may download this document from the website.
        Organizers and the owner always may; others only once it is approved
        and its visibility covers one of their roles in the event."""
        self.ensure_one()
        doc = self.sudo()
        if user.has_group('event.group_event_registration_desk'):
            return True
        partner = user.partner_id if not user._is_public() else self.env['res.partner']
        if partner and doc.partner_id == partner:
            return True
        if doc.state != 'approved' or doc.visibility == 'organizers':
            return False
        if doc.visibility == 'public':
            return True
        roles = doc.event_id._conference_partner_roles(partner)
        if doc.visibility == 'speakers':
            return 'speaker' in roles
        return bool(roles & {'attendee', 'speaker'})

from odoo import _, api, fields, models

DEFAULT_EXTENSIONS = 'pdf,ppt,pptx,pps,ppsx,key,odp,doc,docx,odt,xls,xlsx,ods,png,jpg,jpeg,mp4,zip'


class EventEvent(models.Model):
    _inherit = 'event.event'

    # Call for papers
    cfp_enabled = fields.Boolean('Call for Papers')
    cfp_deadline = fields.Datetime('Submission Deadline')
    cfp_instructions = fields.Html('Submission Guidelines', translate=True)
    cfp_blind_review = fields.Boolean(
        'Blind Review', default=True,
        help="Reviewers do not see who submitted the paper.")
    cfp_min_reviews = fields.Integer(
        'Reviewers per Submission', default=2,
        help="Number of reviewers assigned by 'Auto-assign reviewers'.")
    cfp_reviewer_ids = fields.Many2many(
        'res.users', 'conference_event_reviewer_rel', 'event_id', 'user_id',
        string='Reviewer Pool', domain=[('share', '=', False)])
    is_cfp_open = fields.Boolean('Submissions Open', compute='_compute_is_cfp_open')
    website_cfp = fields.Boolean(
        'Call for Papers on Website', compute='_compute_website_cfp',
        readonly=False, store=True)
    cfp_menu_ids = fields.One2many(
        'website.event.menu', 'event_id', string='Call for Papers Menus',
        domain=[('menu_type', '=', 'conference_cfp')])

    # Documents
    doc_upload_enabled = fields.Boolean('Portal Uploads', default=True)
    doc_upload_deadline = fields.Datetime('Upload Deadline')
    doc_attendee_upload = fields.Boolean(
        'Attendees Can Upload',
        help="Registered attendees may upload files from their portal, "
             "not only speakers and authors.")
    doc_max_size_mb = fields.Integer('Max File Size (MB)', default=50)
    doc_allowed_extensions = fields.Char(
        'Allowed File Types', default=DEFAULT_EXTENSIONS,
        help="Comma-separated extensions. Leave empty to allow any type.")
    website_materials = fields.Boolean(
        'Materials Page on Website', compute='_compute_website_materials',
        readonly=False, store=True)
    materials_menu_ids = fields.One2many(
        'website.event.menu', 'event_id', string='Materials Menus',
        domain=[('menu_type', '=', 'conference_materials')])

    submission_ids = fields.One2many('conference.submission', 'event_id', string='Submissions')
    submission_count = fields.Integer(compute='_compute_conference_counts')
    document_ids = fields.One2many('conference.document', 'event_id', string='Documents')
    document_count = fields.Integer(compute='_compute_conference_counts')
    document_pending_count = fields.Integer(compute='_compute_conference_counts')

    _cfp_min_reviews_positive = models.Constraint(
        'CHECK(cfp_min_reviews >= 0)', 'Reviewers per submission cannot be negative.')
    _doc_max_size_positive = models.Constraint(
        'CHECK(doc_max_size_mb >= 0)', 'Maximum file size cannot be negative.')

    @api.depends('cfp_enabled', 'cfp_deadline')
    def _compute_is_cfp_open(self):
        now = fields.Datetime.now()
        for event in self:
            event.is_cfp_open = bool(
                event.cfp_enabled and (not event.cfp_deadline or event.cfp_deadline >= now))

    @api.depends('cfp_enabled', 'website_menu')
    def _compute_website_cfp(self):
        for event in self:
            event.website_cfp = bool(event.cfp_enabled and event.website_menu)

    @api.depends('website_menu')
    def _compute_website_materials(self):
        for event in self:
            event.website_materials = event.website_menu

    def _compute_conference_counts(self):
        sub_data = dict(self.env['conference.submission']._read_group(
            [('event_id', 'in', self.ids)], ['event_id'], ['__count']))
        doc_data = {}
        for event, state, count in self.env['conference.document']._read_group(
                [('event_id', 'in', self.ids)], ['event_id', 'state'], ['__count']):
            doc_data.setdefault(event, {})[state] = count
        for event in self:
            docs = doc_data.get(event, {})
            event.submission_count = sub_data.get(event, 0)
            event.document_count = sum(docs.values())
            event.document_pending_count = docs.get('pending', 0)

    # ------------------------------------------------------------
    # Website menus
    # ------------------------------------------------------------

    def _get_menu_update_fields(self):
        return super()._get_menu_update_fields() + ['website_cfp', 'website_materials']

    def _update_website_menus(self, menus_update_by_field=None):
        super()._update_website_menus(menus_update_by_field=menus_update_by_field)
        for event in self:
            if not event.menu_id:
                continue
            if not menus_update_by_field or event in menus_update_by_field.get('website_cfp', []):
                event._update_website_menu_entry('website_cfp', 'cfp_menu_ids', 'conference_cfp')
            if not menus_update_by_field or event in menus_update_by_field.get('website_materials', []):
                event._update_website_menu_entry(
                    'website_materials', 'materials_menu_ids', 'conference_materials')

    def _get_menu_type_field_matching(self):
        res = super()._get_menu_type_field_matching()
        res['conference_cfp'] = 'website_cfp'
        res['conference_materials'] = 'website_materials'
        return res

    def _get_website_menu_entries(self):
        self.ensure_one()
        slug = self.env['ir.http']._slug(self)
        return super()._get_website_menu_entries() + [
            (_('Call for Papers'), '/event/%s/call-for-papers' % slug, False, 22, 'conference_cfp', False),
            (_('Materials'), '/event/%s/materials' % slug, False, 40, 'conference_materials', False),
        ]

    # ------------------------------------------------------------
    # Participation helpers (used by portal and download checks)
    # ------------------------------------------------------------

    def _conference_allowed_extensions(self):
        self.ensure_one()
        return {
            ext.strip().lower().lstrip('.')
            for ext in (self.doc_allowed_extensions or '').split(',')
            if ext.strip()
        }

    def _conference_is_upload_open(self):
        self.ensure_one()
        return bool(self.doc_upload_enabled and (
            not self.doc_upload_deadline or self.doc_upload_deadline >= fields.Datetime.now()))

    def _conference_partner_registrations(self, partner):
        self.ensure_one()
        domain = [('event_id', '=', self.id), ('state', 'in', ('open', 'done'))]
        if partner.email:
            domain += ['|', ('partner_id', '=', partner.id), ('email', '=ilike', partner.email)]
        else:
            domain += [('partner_id', '=', partner.id)]
        return self.env['event.registration'].sudo().search(domain)

    def _conference_partner_tracks(self, partner):
        self.ensure_one()
        return self.env['event.track'].sudo().search([
            ('event_id', '=', self.id), ('partner_id', '=', partner.id)])

    def _conference_partner_roles(self, partner):
        """Return the set of roles a partner holds in this event:
        'attendee', 'speaker' and/or 'author'."""
        self.ensure_one()
        roles = set()
        if not partner:
            return roles
        if self._conference_partner_registrations(partner):
            roles.add('attendee')
        if self._conference_partner_tracks(partner):
            roles.add('speaker')
        if self.env['conference.submission'].sudo().search_count([
                ('event_id', '=', self.id), ('partner_id', '=', partner.id),
                ('state', 'not in', ('draft', 'withdrawn'))], limit=1):
            roles.add('author')
        return roles

    def _conference_partner_can_upload(self, partner):
        self.ensure_one()
        if not self._conference_is_upload_open():
            return False
        roles = self._conference_partner_roles(partner)
        if roles & {'speaker', 'author'}:
            return True
        return 'attendee' in roles and self.doc_attendee_upload

    # ------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------

    def action_view_conference_submissions(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id(
            'conference_management.conference_submission_action')
        action['domain'] = [('event_id', '=', self.id)]
        action['context'] = {'default_event_id': self.id}
        return action

    def action_view_conference_documents(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id(
            'conference_management.conference_document_action')
        action['domain'] = [('event_id', '=', self.id)]
        action['context'] = {'default_event_id': self.id}
        return action

    def action_open_av_pack(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Download AV Pack'),
            'res_model': 'conference.av.pack',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_event_id': self.id},
        }

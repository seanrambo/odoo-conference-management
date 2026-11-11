from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import plaintext2html

SUBMISSION_TYPES = [
    ('talk', 'Talk'),
    ('workshop', 'Workshop'),
    ('poster', 'Poster'),
    ('panel', 'Panel'),
    ('keynote', 'Keynote'),
]

SUBMISSION_STATES = [
    ('draft', 'Draft'),
    ('submitted', 'Submitted'),
    ('under_review', 'Under Review'),
    ('revision', 'Revision Requested'),
    ('accepted', 'Accepted'),
    ('rejected', 'Rejected'),
    ('withdrawn', 'Withdrawn'),
]


class ConferenceSubmission(models.Model):
    _name = 'conference.submission'
    _description = 'Conference Submission'
    _inherit = ['portal.mixin', 'mail.thread', 'mail.activity.mixin']
    _order = 'submit_date desc, id desc'

    name = fields.Char('Title', required=True, tracking=True)
    reference = fields.Char('Reference', readonly=True, copy=False, default=lambda self: _('New'))
    event_id = fields.Many2one(
        'event.event', 'Event', required=True, index=True, tracking=True, ondelete='cascade')
    company_id = fields.Many2one(related='event_id.company_id', store=True)
    partner_id = fields.Many2one(
        'res.partner', 'Submitted By', required=True, index=True, tracking=True,
        default=lambda self: self.env.user.partner_id)
    partner_email = fields.Char(related='partner_id.email')
    coauthors = fields.Text('Co-authors', help="One per line, with affiliation.")
    submission_type = fields.Selection(SUBMISSION_TYPES, 'Session Type', required=True, default='talk')
    duration = fields.Float('Requested Duration', default=0.5, help="In hours.")
    keywords = fields.Char('Keywords')
    tag_ids = fields.Many2many('event.track.tag', string='Topics')
    abstract = fields.Text('Abstract', required=True)
    state = fields.Selection(
        SUBMISSION_STATES, 'Status', default='draft', required=True,
        tracking=True, index=True, copy=False, group_expand=True)
    submit_date = fields.Datetime('Submitted On', readonly=True, copy=False)
    decision_date = fields.Datetime('Decided On', readonly=True, copy=False)
    decision_note = fields.Text(
        'Message to Author', copy=False,
        help="Included in the decision email together with reviewer comments for the author.")
    color = fields.Integer()

    review_ids = fields.One2many('conference.review', 'submission_id', string='Reviews')
    review_count = fields.Integer('Reviewers', compute='_compute_review_stats', store=True)
    review_done_count = fields.Integer('Completed Reviews', compute='_compute_review_stats', store=True)
    average_score = fields.Float(
        'Average Score', compute='_compute_review_stats', store=True, aggregator='avg',
        digits=(3, 2))
    track_id = fields.Many2one('event.track', 'Scheduled Talk', readonly=True, copy=False)
    document_ids = fields.One2many('conference.document', 'submission_id', string='Files')
    document_count = fields.Integer(compute='_compute_document_count')
    is_blind_review = fields.Boolean(related='event_id.cfp_blind_review')

    _event_partner_idx = models.Index('(event_id, partner_id)')

    @api.depends('review_ids.state', 'review_ids.score')
    def _compute_review_stats(self):
        for submission in self:
            active = submission.review_ids.filtered(lambda r: r.state != 'declined')
            done = active.filtered(lambda r: r.state == 'done')
            submission.review_count = len(active)
            submission.review_done_count = len(done)
            submission.average_score = (
                sum(done.mapped('score')) / len(done) if done else 0.0)

    def _compute_document_count(self):
        data = dict(self.env['conference.document']._read_group(
            [('submission_id', 'in', self.ids)], ['submission_id'], ['__count']))
        for submission in self:
            submission.document_count = data.get(submission, 0)

    def _compute_access_url(self):
        super()._compute_access_url()
        for submission in self:
            submission.access_url = '/my/submissions/%s' % submission.id

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('reference') or vals['reference'] == _('New'):
                vals['reference'] = self.env['ir.sequence'].next_by_code(
                    'conference.submission') or _('New')
        submissions = super().create(vals_list)
        for submission in submissions:
            submission._message_subscribe(partner_ids=submission.partner_id.ids)
        return submissions

    @api.depends('reference', 'name')
    def _compute_display_name(self):
        for submission in self:
            submission.display_name = (
                '%s - %s' % (submission.reference, submission.name)
                if submission.reference else submission.name)

    # ------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------

    def _send_template(self, xmlid):
        template = self.env.ref(xmlid, raise_if_not_found=False)
        if not template:
            return
        for submission in self:
            submission.message_post_with_source(
                template, subtype_xmlid='mail.mt_comment',
                email_layout_xmlid='mail.mail_notification_light')

    def action_submit(self):
        for submission in self:
            if submission.state not in ('draft', 'revision'):
                raise UserError(_("Only draft or revision-requested submissions can be submitted."))
            if not (submission.abstract or '').strip():
                raise UserError(_("Please add an abstract before submitting."))
        resubmitted = self.filtered(lambda s: s.state == 'revision')
        resubmitted.write({'state': 'under_review', 'submit_date': fields.Datetime.now()})
        # A revised paper goes back to its reviewers.
        resubmitted.review_ids.filtered(lambda r: r.state == 'done').write({'state': 'assigned'})
        for review in resubmitted.review_ids.filtered(lambda r: r.state == 'assigned'):
            review._schedule_reviewer_activity()
        new = self - resubmitted
        new.write({'state': 'submitted', 'submit_date': fields.Datetime.now()})
        self._send_template('conference_management.mail_template_submission_received')
        return True

    def action_start_review(self):
        for submission in self:
            if submission.state != 'submitted':
                raise UserError(_("Only submitted papers can be put under review."))
            if not submission.review_ids.filtered(lambda r: r.state != 'declined'):
                raise UserError(_("Assign at least one reviewer to %s first.", submission.display_name))
        self.write({'state': 'under_review'})
        return True

    def action_auto_assign_reviewers(self):
        """Assign reviewers from the event pool, least-loaded first, until each
        submission has the event's required number of active reviews.
        The submitter is never assigned to their own paper."""
        Review = self.env['conference.review']
        for submission in self:
            if submission.state not in ('submitted', 'under_review'):
                raise UserError(_("Reviewers can only be assigned to submitted papers."))
            event = submission.event_id
            pool = event.cfp_reviewer_ids.filtered(
                lambda u: u.partner_id != submission.partner_id)
            if not pool:
                raise UserError(_("Add reviewers to the reviewer pool of %s first.", event.name))
            already = submission.review_ids.reviewer_id
            missing = event.cfp_min_reviews - len(
                submission.review_ids.filtered(lambda r: r.state != 'declined'))
            candidates = pool - already
            if missing <= 0 or not candidates:
                continue
            load = dict(Review._read_group(
                [('event_id', '=', event.id), ('reviewer_id', 'in', candidates.ids),
                 ('state', '!=', 'declined')],
                ['reviewer_id'], ['__count']))
            ordered = candidates.sorted(lambda u: (load.get(u, 0), u.id))
            Review.create([
                {'submission_id': submission.id, 'reviewer_id': user.id}
                for user in ordered[:missing]
            ])
        return True

    def action_request_revision(self):
        self._check_decidable()
        self.write({'state': 'revision', 'decision_date': fields.Datetime.now()})
        self._send_template('conference_management.mail_template_submission_revision')
        return True

    def action_reject(self):
        self._check_decidable()
        self.write({'state': 'rejected', 'decision_date': fields.Datetime.now()})
        self._send_template('conference_management.mail_template_submission_rejected')
        return True

    def action_accept(self):
        self._check_decidable()
        for submission in self:
            submission.track_id = submission._create_track()
            submission.document_ids.filtered(lambda d: not d.track_id).track_id = submission.track_id
        self.write({'state': 'accepted', 'decision_date': fields.Datetime.now()})
        self._send_template('conference_management.mail_template_submission_accepted')
        return True

    def _check_decidable(self):
        for submission in self:
            if submission.state not in ('submitted', 'under_review'):
                raise UserError(_(
                    "%s cannot be decided in its current status.", submission.display_name))

    def _create_track(self):
        self.ensure_one()
        if self.track_id:
            return self.track_id
        stage = self.env.ref('website_event_track.event_track_stage1', raise_if_not_found=False)
        vals = {
            'name': self.name,
            'event_id': self.event_id.id,
            'partner_id': self.partner_id.id,
            'description': plaintext2html(self.abstract or ''),
            'duration': self.duration or 0.5,
            'tag_ids': [(6, 0, self.tag_ids.ids)],
        }
        if stage:
            vals['stage_id'] = stage.id
        # The acceptance email replaces the track's own confirmation email.
        return self.env['event.track'].with_context(
            mail_create_nolog=True, mail_notrack=True).create(vals)

    def action_withdraw(self):
        for submission in self:
            if submission.state in ('accepted', 'rejected', 'withdrawn'):
                raise UserError(_("%s can no longer be withdrawn.", submission.display_name))
        self.write({'state': 'withdrawn'})
        self.review_ids.filtered(lambda r: r.state == 'assigned').activity_unlink(
            ['mail.mail_activity_data_todo'])
        return True

    def action_reset_draft(self):
        self.filtered(lambda s: s.state in ('withdrawn', 'rejected')).write({'state': 'draft'})
        return True

    def action_view_documents(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id(
            'conference_management.conference_document_action')
        action['domain'] = [('submission_id', '=', self.id)]
        action['context'] = {
            'default_submission_id': self.id,
            'default_event_id': self.event_id.id,
            'default_partner_id': self.partner_id.id,
            'default_document_type': 'paper',
        }
        return action

    def action_view_track(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'event.track',
            'res_id': self.track_id.id,
            'view_mode': 'form',
        }

    # Comments written for the author, used in decision emails and the portal.
    def _get_author_feedback(self):
        self.ensure_one()
        return [
            review.comment_author
            for review in self.sudo().review_ids
            if review.state == 'done' and (review.comment_author or '').strip()
        ]

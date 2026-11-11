from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

SCORES = [('1', '1 - Poor'), ('2', '2 - Fair'), ('3', '3 - Good'), ('4', '4 - Very good'), ('5', '5 - Excellent')]
SCORE_FIELDS = ('score_originality', 'score_relevance', 'score_clarity', 'score_quality')
RECOMMENDATIONS = [
    ('strong_accept', 'Strong Accept'),
    ('accept', 'Accept'),
    ('weak_accept', 'Weak Accept'),
    ('weak_reject', 'Weak Reject'),
    ('reject', 'Reject'),
]
# Fields a reviewer may change on their own review.
REVIEWER_WRITABLE = set(SCORE_FIELDS) | {
    'recommendation', 'comment_author', 'comment_committee', 'conflict_of_interest',
}


class ConferenceReview(models.Model):
    _name = 'conference.review'
    _description = 'Conference Review'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'state, id desc'

    submission_id = fields.Many2one(
        'conference.submission', 'Submission', required=True, index=True, ondelete='cascade')
    event_id = fields.Many2one(related='submission_id.event_id', store=True, index=True)
    company_id = fields.Many2one(related='submission_id.company_id', store=True)
    reviewer_id = fields.Many2one(
        'res.users', 'Reviewer', required=True, index=True, tracking=True,
        domain=[('share', '=', False)], default=lambda self: self.env.user)
    state = fields.Selection([
        ('assigned', 'To Review'),
        ('done', 'Completed'),
        ('declined', 'Declined'),
    ], 'Status', default='assigned', required=True, tracking=True, copy=False)
    score_originality = fields.Selection(SCORES, 'Originality')
    score_relevance = fields.Selection(SCORES, 'Relevance')
    score_clarity = fields.Selection(SCORES, 'Clarity')
    score_quality = fields.Selection(SCORES, 'Technical Quality')
    score = fields.Float(
        'Score', compute='_compute_score', store=True, aggregator='avg', digits=(3, 2))
    recommendation = fields.Selection(RECOMMENDATIONS, 'Recommendation', tracking=True)
    comment_author = fields.Text(
        'Comments for the Author', help="Sent to the author with the decision.")
    comment_committee = fields.Text(
        'Confidential Comments', help="Only organizers and the committee see these.")
    conflict_of_interest = fields.Boolean(
        'Conflict of Interest',
        help="Tick and decline if you know the author or cannot review impartially.")
    date_done = fields.Datetime('Completed On', readonly=True, copy=False)

    # Submission content exposed to reviewers, who have no direct access to
    # conference.submission (keeps blind review effective).
    submission_name = fields.Char(related='submission_id.name', string='Title')
    submission_reference = fields.Char(related='submission_id.reference', string='Reference')
    submission_abstract = fields.Text(related='submission_id.abstract', string='Abstract')
    submission_type = fields.Selection(related='submission_id.submission_type')
    submission_keywords = fields.Char(related='submission_id.keywords', string='Keywords')
    submission_state = fields.Selection(related='submission_id.state', string='Submission Status')
    submission_document_ids = fields.One2many(
        related='submission_id.document_ids', string='Submission Files')
    author_display = fields.Char('Author', compute='_compute_author_display', compute_sudo=True)

    _submission_reviewer_unique = models.Constraint(
        'UNIQUE(submission_id, reviewer_id)',
        'This reviewer is already assigned to that submission.')

    @api.depends(*SCORE_FIELDS)
    def _compute_score(self):
        for review in self:
            values = [int(review[f]) for f in SCORE_FIELDS if review[f]]
            review.score = sum(values) / len(values) if values else 0.0

    @api.depends('submission_id.partner_id', 'submission_id.coauthors', 'event_id.cfp_blind_review')
    @api.depends_context('uid')
    def _compute_author_display(self):
        is_organizer = self.env.user.has_group('event.group_event_user')
        for review in self:
            submission = review.submission_id
            if review.event_id.cfp_blind_review and not is_organizer:
                review.author_display = _('Hidden (blind review)')
            else:
                names = [submission.partner_id.display_name or '']
                if submission.coauthors:
                    names += [line.strip() for line in submission.coauthors.splitlines() if line.strip()]
                review.author_display = ', '.join(n for n in names if n)

    @api.depends('submission_id.reference', 'reviewer_id')
    def _compute_display_name(self):
        for review in self:
            review.display_name = '%s / %s' % (
                review.submission_id.sudo().reference or '', review.reviewer_id.name or '')

    @api.model_create_multi
    def create(self, vals_list):
        reviews = super().create(vals_list)
        for review in reviews:
            submission = review.submission_id
            if submission.partner_id == review.reviewer_id.partner_id:
                raise UserError(_("Authors cannot review their own submission."))
            if submission.state not in ('submitted', 'under_review'):
                raise UserError(_("Reviewers can only be assigned to submitted papers."))
            review._schedule_reviewer_activity()
        reviews.submission_id.filtered(lambda s: s.state == 'submitted').write({'state': 'under_review'})
        return reviews

    def write(self, vals):
        if not self.env.su and not self.env.user.has_group('event.group_event_user'):
            forbidden = set(vals) - REVIEWER_WRITABLE - {'state', 'date_done'}
            if forbidden:
                raise AccessError(_("Reviewers can only change scores and comments."))
            # submission_state is a sudo related field: reviewers cannot read submissions.
            if any(r.state == 'done' or r.submission_state != 'under_review' for r in self):
                raise UserError(_("This review is closed and can no longer be changed."))
        return super().write(vals)

    def _schedule_reviewer_activity(self):
        for review in self:
            review.activity_schedule(
                'mail.mail_activity_data_todo',
                date_deadline=fields.Date.context_today(review),
                summary=_('Review %s', review.submission_id.reference),
                user_id=review.reviewer_id.id,
            )

    def action_submit_review(self):
        for review in self:
            if review.state != 'assigned':
                raise UserError(_("This review is already closed."))
            if review.conflict_of_interest:
                raise UserError(_("You flagged a conflict of interest. Decline the review instead."))
            missing = [review._fields[f].string for f in SCORE_FIELDS if not review[f]]
            if missing or not review.recommendation:
                raise UserError(_(
                    "Please score every criterion and give a recommendation. Missing: %s",
                    ', '.join(missing + ([] if review.recommendation else [_('Recommendation')]))))
        self.write({'state': 'done', 'date_done': fields.Datetime.now()})
        self.activity_feedback(['mail.mail_activity_data_todo'])
        for review in self:
            review.submission_id.sudo().message_post(
                body=_("Review completed by %(reviewer)s: %(score).2f / 5, %(rec)s",
                       reviewer=review.reviewer_id.name, score=review.score,
                       rec=dict(RECOMMENDATIONS).get(review.recommendation)),
                subtype_xmlid='mail.mt_note')
        return True

    def action_decline(self):
        for review in self:
            if review.state != 'assigned':
                raise UserError(_("Only open reviews can be declined."))
        self.write({'state': 'declined'})
        self.activity_unlink(['mail.mail_activity_data_todo'])
        for review in self:
            review.submission_id.sudo().message_post(
                body=_("%(reviewer)s declined the review%(coi)s. Assign another reviewer.",
                       reviewer=review.reviewer_id.name,
                       coi=_(" (conflict of interest)") if review.conflict_of_interest else ''),
                subtype_xmlid='mail.mt_note')
        return True

    def action_reopen(self):
        self.filtered(lambda r: r.state != 'assigned').write({'state': 'assigned', 'date_done': False})
        self._schedule_reviewer_activity()
        return True

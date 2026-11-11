import base64
from urllib.parse import urlencode

from odoo import _, fields, http
from odoo.exceptions import AccessError, MissingError, UserError, ValidationError
from odoo.http import request

from odoo.addons.portal.controllers.portal import CustomerPortal, pager as portal_pager

from ..models.conference_document import DOCUMENT_TYPES, VISIBILITY
from ..models.conference_submission import SUBMISSION_TYPES

# Visibility choices offered to portal uploaders (organizers still approve).
PORTAL_VISIBILITY = [v for v in VISIBILITY if v[0] != 'public']
PORTAL_DOC_TYPES = [t for t in DOCUMENT_TYPES if t[0] != 'sponsor']


def _redirect(url, **params):
    params = {k: v for k, v in params.items() if v}
    return request.redirect('%s?%s' % (url, urlencode(params)) if params else url)


class ConferencePortal(CustomerPortal):

    # ------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------

    def _conf_partner(self):
        return request.env.user.partner_id

    def _conf_read_upload(self, upload, event):
        """Return (base64 data, filename) from a werkzeug FileStorage, or
        raise UserError with a message the uploader can act on."""
        if not upload or not getattr(upload, 'filename', None):
            raise UserError(_("Please choose a file to upload."))
        raw = upload.read()
        if not raw:
            raise UserError(_("The uploaded file is empty."))
        limit = event.doc_max_size_mb
        if limit and len(raw) > limit * 1024 * 1024:
            raise UserError(_("The file is larger than the %s MB limit.", limit))
        return base64.b64encode(raw), upload.filename

    def _conf_submission_events(self):
        return request.env['event.event'].sudo().search([
            ('cfp_enabled', '=', True), ('website_published', '=', True),
        ]).filtered('is_cfp_open')

    def _conf_upload_events(self, partner):
        # Uploads stay possible for a while after the event (post-event slides).
        since = fields.Datetime.subtract(fields.Datetime.now(), days=90)
        events = request.env['event.event'].sudo().search([
            ('doc_upload_enabled', '=', True), ('date_end', '>=', since),
        ])
        return events.filtered(lambda e: e._conference_partner_can_upload(partner))

    def _conf_own_submission(self, submission_id, access_token=None):
        return self._document_check_access('conference.submission', submission_id, access_token)

    # ------------------------------------------------------------
    # Portal home
    # ------------------------------------------------------------

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        partner = self._conf_partner()
        if 'conference_submission_count' in counters:
            values['conference_submission_count'] = request.env['conference.submission'].sudo().search_count(
                [('partner_id', '=', partner.id)])
        if 'conference_document_count' in counters:
            values['conference_document_count'] = request.env['conference.document'].sudo().search_count(
                [('partner_id', '=', partner.id)])
        return values

    # ------------------------------------------------------------
    # Submissions
    # ------------------------------------------------------------

    @http.route(['/my/submissions', '/my/submissions/page/<int:page>'],
                type='http', auth='user', website=True)
    def portal_my_submissions(self, page=1, **kw):
        Submission = request.env['conference.submission'].sudo()
        domain = [('partner_id', '=', self._conf_partner().id)]
        total = Submission.search_count(domain)
        pager = portal_pager(url='/my/submissions', total=total, page=page, step=self._items_per_page)
        submissions = Submission.search(domain, limit=self._items_per_page, offset=pager['offset'])
        values = self._prepare_portal_layout_values()
        values.update({
            'submissions': submissions,
            'pager': pager,
            'page_name': 'conference_submissions',
            'open_events': self._conf_submission_events(),
        })
        return request.render('conference_management.portal_my_submissions', values)

    @http.route('/my/submissions/new', type='http', auth='user', website=True, methods=['GET'])
    def portal_submission_new(self, event_id=None, error=None, **kw):
        events = self._conf_submission_events()
        values = self._prepare_portal_layout_values()
        values.update({
            'page_name': 'conference_submission_new',
            'events': events,
            'selected_event': events.filtered(lambda e: str(e.id) == str(event_id))[:1] or events[:1],
            'submission': request.env['conference.submission'],
            'submission_types': SUBMISSION_TYPES,
            'error': error,
        })
        return request.render('conference_management.portal_submission_form', values)

    def _conf_submission_vals(self, post):
        title = (post.get('name') or '').strip()
        abstract = (post.get('abstract') or '').strip()
        if not title or not abstract:
            raise UserError(_("Title and abstract are required."))
        submission_type = post.get('submission_type')
        if submission_type not in dict(SUBMISSION_TYPES):
            submission_type = 'talk'
        try:
            duration = max(0.25, min(8.0, float(post.get('duration') or 0.5)))
        except ValueError:
            duration = 0.5
        return {
            'name': title,
            'abstract': abstract,
            'submission_type': submission_type,
            'duration': duration,
            'keywords': (post.get('keywords') or '').strip(),
            'coauthors': (post.get('coauthors') or '').strip(),
        }

    @http.route('/my/submissions/new', type='http', auth='user', website=True, methods=['POST'])
    def portal_submission_create(self, event_id=None, **post):
        event = self._conf_submission_events().filtered(lambda e: str(e.id) == str(event_id))
        if not event:
            return _redirect('/my/submissions/new', error=_("This call for papers is closed."))
        try:
            # Savepoint: a failed constraint must not leave half-created records.
            with request.env.cr.savepoint():
                vals = self._conf_submission_vals(post)
                vals.update({'event_id': event.id, 'partner_id': self._conf_partner().id})
                submission = request.env['conference.submission'].sudo().create(vals)
                upload = request.httprequest.files.get('file')
                if upload and upload.filename:
                    self._conf_attach_to_submission(submission, upload, post)
                if post.get('action') == 'submit':
                    submission.action_submit()
        except (UserError, ValidationError) as e:
            return _redirect('/my/submissions/new', event_id=event.id, error=str(e))
        return request.redirect(submission.get_portal_url())

    @http.route('/my/submissions/<int:submission_id>', type='http', auth='user', website=True)
    def portal_submission_page(self, submission_id, access_token=None, error=None, message=None, **kw):
        try:
            submission = self._conf_own_submission(submission_id, access_token)
        except (AccessError, MissingError):
            return request.redirect('/my/submissions')
        values = self._prepare_portal_layout_values()
        values.update({
            'submission': submission,
            'page_name': 'conference_submission',
            'editable': submission.state in ('draft', 'revision') and (
                submission.state == 'revision' or submission.event_id.is_cfp_open),
            'feedback': submission._get_author_feedback() if submission.state in (
                'accepted', 'rejected', 'revision') else [],
            'submission_types': SUBMISSION_TYPES,
            'doc_types': PORTAL_DOC_TYPES,
            'error': error,
            'message': message,
        })
        values = self._get_page_view_values(
            submission, access_token, values, 'my_conference_submissions_history', False, **kw)
        return request.render('conference_management.portal_submission_page', values)

    @http.route('/my/submissions/<int:submission_id>/save', type='http', auth='user',
                website=True, methods=['POST'])
    def portal_submission_save(self, submission_id, **post):
        try:
            submission = self._conf_own_submission(submission_id)
        except (AccessError, MissingError):
            return request.redirect('/my/submissions')
        url = '/my/submissions/%s' % submission.id
        if submission.state not in ('draft', 'revision'):
            return _redirect(url, error=_("This submission can no longer be edited."))
        if submission.state == 'draft' and not submission.event_id.is_cfp_open:
            return _redirect(url, error=_("The submission deadline has passed."))
        try:
            with request.env.cr.savepoint():
                submission.write(self._conf_submission_vals(post))
                if post.get('action') == 'submit':
                    submission.action_submit()
                    return _redirect(url, message=_("Thank you. Your submission has been received."))
        except (UserError, ValidationError) as e:
            return _redirect(url, error=str(e))
        return _redirect(url, message=_("Changes saved."))

    @http.route('/my/submissions/<int:submission_id>/withdraw', type='http', auth='user',
                website=True, methods=['POST'])
    def portal_submission_withdraw(self, submission_id, **post):
        try:
            submission = self._conf_own_submission(submission_id)
            submission.action_withdraw()
        except (AccessError, MissingError):
            return request.redirect('/my/submissions')
        except UserError as e:
            return _redirect('/my/submissions/%s' % submission_id, error=str(e))
        return _redirect('/my/submissions/%s' % submission_id, message=_("Submission withdrawn."))

    def _conf_attach_to_submission(self, submission, upload, post):
        data, filename = self._conf_read_upload(upload, submission.event_id)
        doc_type = post.get('document_type')
        if doc_type not in dict(PORTAL_DOC_TYPES):
            doc_type = 'paper'
        return request.env['conference.document'].sudo().create({
            'name': (post.get('document_name') or '').strip() or filename.rsplit('.', 1)[0],
            'event_id': submission.event_id.id,
            'submission_id': submission.id,
            'track_id': submission.track_id.id,
            'partner_id': submission.partner_id.id,
            'document_type': doc_type,
            'visibility': 'organizers',
            'file': data,
            'filename': filename,
            'uploaded_via_portal': True,
        })

    @http.route('/my/submissions/<int:submission_id>/upload', type='http', auth='user',
                website=True, methods=['POST'])
    def portal_submission_upload(self, submission_id, **post):
        try:
            submission = self._conf_own_submission(submission_id)
        except (AccessError, MissingError):
            return request.redirect('/my/submissions')
        url = '/my/submissions/%s' % submission.id
        if submission.state in ('rejected', 'withdrawn'):
            return _redirect(url, error=_("Files can no longer be added to this submission."))
        try:
            with request.env.cr.savepoint():
                self._conf_attach_to_submission(submission, request.httprequest.files.get('file'), post)
        except (UserError, ValidationError) as e:
            return _redirect(url, error=str(e))
        return _redirect(url, message=_("File uploaded."))

    # ------------------------------------------------------------
    # Documents (speakers, authors, attendees)
    # ------------------------------------------------------------

    @http.route(['/my/conference/documents', '/my/conference/documents/page/<int:page>'],
                type='http', auth='user', website=True)
    def portal_my_documents(self, page=1, error=None, message=None, **kw):
        partner = self._conf_partner()
        Document = request.env['conference.document'].sudo()
        domain = [('partner_id', '=', partner.id)]
        total = Document.search_count(domain)
        pager = portal_pager(url='/my/conference/documents', total=total, page=page, step=self._items_per_page)
        upload_events = self._conf_upload_events(partner)
        tracks = request.env['event.track'].sudo().search([
            ('partner_id', '=', partner.id), ('event_id', 'in', upload_events.ids)])
        values = self._prepare_portal_layout_values()
        values.update({
            'documents': Document.search(domain, limit=self._items_per_page, offset=pager['offset']),
            'pager': pager,
            'page_name': 'conference_documents',
            'upload_events': upload_events,
            'tracks': tracks,
            'doc_types': PORTAL_DOC_TYPES,
            'visibilities': PORTAL_VISIBILITY,
            'error': error,
            'message': message,
        })
        return request.render('conference_management.portal_my_documents', values)

    @http.route('/my/conference/documents/upload', type='http', auth='user', website=True, methods=['POST'])
    def portal_document_upload(self, event_id=None, track_id=None, **post):
        url = '/my/conference/documents'
        partner = self._conf_partner()
        event = self._conf_upload_events(partner).filtered(lambda e: str(e.id) == str(event_id))
        if not event:
            return _redirect(url, error=_("Uploads are closed for this event, or you are not taking part in it."))
        track = request.env['event.track'].sudo().browse(int(track_id)).exists() if track_id else None
        if track and (track.partner_id != partner or track.event_id != event):
            track = None
        registration = event._conference_partner_registrations(partner)[:1]
        doc_type = post.get('document_type') if post.get('document_type') in dict(PORTAL_DOC_TYPES) else 'other'
        visibility = post.get('visibility') if post.get('visibility') in dict(PORTAL_VISIBILITY) else 'organizers'
        try:
            with request.env.cr.savepoint():
                data, filename = self._conf_read_upload(request.httprequest.files.get('file'), event)
                request.env['conference.document'].sudo().create({
                    'name': (post.get('name') or '').strip() or filename.rsplit('.', 1)[0],
                    'description': (post.get('description') or '').strip(),
                    'event_id': event.id,
                    'track_id': track.id if track else False,
                    'registration_id': registration.id,
                    'partner_id': partner.id,
                    'document_type': doc_type,
                    'visibility': visibility,
                    'file': data,
                    'filename': filename,
                    'uploaded_via_portal': True,
                })
        except (UserError, ValidationError) as e:
            return _redirect(url, error=str(e))
        return _redirect(url, message=_("File uploaded. The organizers will review it shortly."))

    @http.route('/my/conference/documents/<int:document_id>/replace', type='http', auth='user',
                website=True, methods=['POST'])
    def portal_document_replace(self, document_id, **post):
        url = '/my/conference/documents'
        doc = request.env['conference.document'].sudo().browse(document_id).exists()
        if not doc or doc.partner_id != self._conf_partner():
            return request.redirect(url)
        if not doc.event_id._conference_is_upload_open():
            return _redirect(url, error=_("The upload deadline for this event has passed."))
        try:
            with request.env.cr.savepoint():
                data, filename = self._conf_read_upload(request.httprequest.files.get('file'), doc.event_id)
                # A replaced file goes back to pending approval.
                doc.write({'file': data, 'filename': filename, 'state': 'pending'})
        except (UserError, ValidationError) as e:
            return _redirect(url, error=str(e))
        return _redirect(url, message=_("New version uploaded. It will be visible again once approved."))

    # ------------------------------------------------------------
    # Downloads
    # ------------------------------------------------------------

    @http.route('/conference/document/<int:document_id>/download', type='http', auth='public', website=True)
    def conference_document_download(self, document_id, **kw):
        doc = request.env['conference.document'].sudo().browse(document_id).exists()
        if not doc or not doc._can_be_downloaded_by(request.env.user):
            raise request.not_found()
        return request.env['ir.binary']._get_stream_from(
            doc, 'file', filename=doc.filename or doc.name,
        ).get_response(as_attachment=True)

    @http.route('/conference/av_pack/<int:wizard_id>', type='http', auth='user')
    def conference_av_pack(self, wizard_id, **kw):
        if not request.env.user.has_group('event.group_event_user'):
            raise request.not_found()
        wizard = request.env['conference.av.pack'].browse(wizard_id).exists()
        if not wizard or wizard.create_uid != request.env.user:
            raise request.not_found()
        content, filename = wizard._build_zip()
        return request.make_response(content, headers=[
            ('Content-Type', 'application/zip'),
            ('Content-Length', str(len(content))),
            ('Content-Disposition', http.content_disposition(filename)),
        ])

    # ------------------------------------------------------------
    # Event website pages
    # ------------------------------------------------------------

    @http.route('/event/<model("event.event"):event>/call-for-papers', type='http',
                auth='public', website=True, sitemap=False)
    def event_call_for_papers(self, event, **kw):
        if not event.cfp_enabled:
            raise request.not_found()
        return request.render('conference_management.event_call_for_papers', {
            'event': event,
            'main_object': event,
        })

    @http.route('/event/<model("event.event"):event>/materials', type='http',
                auth='public', website=True, sitemap=False)
    def event_materials(self, event, **kw):
        user = request.env.user
        docs = request.env['conference.document'].sudo().search([
            ('event_id', '=', event.id), ('state', '=', 'approved'),
            ('visibility', '!=', 'organizers'),
        ]).filtered(lambda d: d._can_be_downloaded_by(user))
        sessions = {}
        for doc in docs.sorted(lambda d: (d.date_start or d.create_date, d.id)):
            sessions.setdefault(doc.track_id, request.env['conference.document'].sudo())
            sessions[doc.track_id] |= doc
        return request.render('conference_management.event_materials', {
            'event': event,
            'main_object': event,
            'sessions': sessions,
            'is_public': user._is_public(),
        })

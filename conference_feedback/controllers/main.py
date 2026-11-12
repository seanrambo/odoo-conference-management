from datetime import timedelta

from werkzeug.exceptions import NotFound

from odoo import fields, http
from odoo.http import request, content_disposition

# How long after the event attendees can still rate sessions.
FEEDBACK_OPEN_DAYS = 30


class ConferenceFeedbackController(http.Controller):

    def _registration_from_token(self, token):
        if not token or len(token) < 16:
            raise NotFound()
        registration = request.env['event.registration'].sudo().search([('feedback_token', '=', token)], limit=1)
        if not registration or registration.state == 'cancel':
            raise NotFound()
        return registration

    @staticmethod
    def _feedback_open(registration):
        end = registration.event_id.date_end
        return not end or fields.Datetime.now() <= end + timedelta(days=FEEDBACK_OPEN_DAYS)

    @http.route('/conference/feedback/<string:token>', type='http', auth='public', website=True,
                sitemap=False, methods=['GET'])
    def feedback_page(self, token, saved=None, **kw):
        registration = self._registration_from_token(token)
        feedbacks = registration.session_feedback_ids.sorted(lambda f: (f.track_id.date or f.create_date, f.id))
        survey_input = registration.feedback_survey_input_id
        certificate = registration.certificate_id if registration.certificate_id.state == 'issued' else None
        return request.render('conference_feedback.feedback_page', {
            'registration': registration,
            'event': registration.event_id,
            'feedbacks': feedbacks,
            'token': token,
            'is_open': self._feedback_open(registration),
            'saved': bool(saved),
            'survey_url': survey_input.get_start_url() if survey_input and survey_input.state != 'done' else False,
            'certificate': certificate,
        })

    @http.route('/conference/feedback/<string:token>', type='http', auth='public', website=True,
                sitemap=False, methods=['POST'])
    def feedback_submit(self, token, **post):
        registration = self._registration_from_token(token)
        if self._feedback_open(registration):
            for feedback in registration.session_feedback_ids:
                rating = post.get('rating_%s' % feedback.id)
                if rating:
                    feedback._record_answer(rating, post.get('comment_%s' % feedback.id))
        return request.redirect('/conference/feedback/%s?saved=1' % token)

    # ------------------------------------------------------------
    # Certificates
    # ------------------------------------------------------------

    def _certificate(self, number, code):
        certificate = request.env['conference.certificate'].sudo().search([('name', '=', number)], limit=1)
        if certificate and certificate._verification_code() == code:
            return certificate
        return None

    @http.route('/conference/certificate/<string:number>/<string:code>', type='http', auth='public',
                website=True, sitemap=False)
    def certificate_verify(self, number, code, **kw):
        certificate = self._certificate(number, code)
        return request.render('conference_feedback.certificate_verify_page', {
            'certificate': certificate,
            'valid': bool(certificate) and certificate.state == 'issued',
            'number': number,
        })

    @http.route('/conference/certificate/<string:number>/<string:code>/pdf', type='http', auth='public',
                sitemap=False)
    def certificate_download(self, number, code, **kw):
        certificate = self._certificate(number, code)
        if not certificate or certificate.state != 'issued':
            raise NotFound()
        pdf, _type = request.env['ir.actions.report'].sudo()._render_qweb_pdf(
            'conference_feedback.action_report_certificate', certificate.ids)
        return request.make_response(pdf, headers=[
            ('Content-Type', 'application/pdf'),
            ('Content-Length', len(pdf)),
            ('Content-Disposition', content_disposition('Certificate %s.pdf' % certificate.name)),
        ])

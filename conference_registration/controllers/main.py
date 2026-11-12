import re
from urllib.parse import urlencode

from odoo import _, fields, http
from odoo.exceptions import UserError, ValidationError
from odoo.http import request

from odoo.addons.portal.controllers.portal import CustomerPortal, pager as portal_pager

TOKEN_RE = re.compile(r'^[0-9a-f]{32}$')


def _redirect(url, **params):
    params = {k: v for k, v in params.items() if v}
    return request.redirect('%s?%s' % (url, urlencode(params)) if params else url)


class ConferenceRegistrationController(http.Controller):

    # ------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------

    def _waitlist_entry(self, token):
        if not TOKEN_RE.match(token or ''):
            return None
        return request.env['conference.waitlist'].sudo().search([('access_token', '=', token)], limit=1) or None

    def _registration(self, token):
        if not TOKEN_RE.match(token or ''):
            return None
        return request.env['event.registration'].sudo().search(
            [('conference_token', '=', token), ('active', 'in', (True, False))], limit=1) or None

    # ------------------------------------------------------------
    # Waitlist
    # ------------------------------------------------------------

    @http.route('/event/<model("event.event"):event>/waitlist', type='http', auth='public',
                website=True, sitemap=False, methods=['GET'])
    def waitlist_form(self, event, error=None, **kw):
        if not event.waitlist_enabled:
            return request.redirect(event.website_url)
        partner = request.env.user.partner_id if not request.env.user._is_public() else None
        return request.render('conference_registration.waitlist_join_page', {
            'event': event,
            'main_object': event,
            'tickets': event.event_ticket_ids,
            'error': error,
            'partner': partner,
        })

    @http.route('/event/<model("event.event"):event>/waitlist', type='http', auth='public',
                website=True, sitemap=False, methods=['POST'])
    def waitlist_join(self, event, name=None, email=None, phone=None, ticket_id=None, website_url=None, **kw):
        url = '/event/%s/waitlist' % event.id
        if website_url:  # honeypot field, hidden from people
            return request.redirect(event.website_url)
        if not event.waitlist_enabled:
            return request.redirect(event.website_url)
        ticket = event.event_ticket_ids.filtered(lambda t: str(t.id) == str(ticket_id))[:1]
        if event.event_ticket_ids and not ticket:
            return _redirect(url, error=_("Choose the ticket you would like."))
        if not (name or '').strip():
            return _redirect(url, error=_("Please enter your name."))
        try:
            with request.env.cr.savepoint():
                entry = request.env['conference.waitlist'].sudo().create({
                    'event_id': event.id,
                    'event_ticket_id': ticket.id,
                    'name': name.strip(),
                    'email': email,
                    'phone': (phone or '').strip(),
                    'partner_id': request.env.user.partner_id.id if not request.env.user._is_public() else False,
                })
        except (UserError, ValidationError) as e:
            return _redirect(url, error=str(e))
        return request.redirect('/event/waitlist/%s' % entry.access_token)

    @http.route('/event/waitlist/<string:token>', type='http', auth='public', website=True, sitemap=False)
    def waitlist_status(self, token, error=None, message=None, **kw):
        entry = self._waitlist_entry(token)
        if not entry:
            raise request.not_found()
        return request.render('conference_registration.waitlist_status_page', {
            'entry': entry,
            'event': entry.event_id,
            'expired': entry.state == 'offered' and entry.offer_deadline and entry.offer_deadline < fields.Datetime.now(),
            'error': error,
            'message': message,
        })

    @http.route('/event/waitlist/<string:token>/claim', type='http', auth='public', website=True,
                sitemap=False, methods=['POST'])
    def waitlist_claim(self, token, **kw):
        entry = self._waitlist_entry(token)
        if not entry:
            raise request.not_found()
        try:
            with request.env.cr.savepoint():
                order = entry._get_or_create_order()
        except (UserError, ValidationError) as e:
            return _redirect('/event/waitlist/%s' % token, error=str(e))
        return request.redirect(order.get_portal_url())

    @http.route('/event/waitlist/<string:token>/leave', type='http', auth='public', website=True,
                sitemap=False, methods=['POST'])
    def waitlist_leave(self, token, **kw):
        entry = self._waitlist_entry(token)
        if not entry:
            raise request.not_found()
        entry.action_cancel()
        return _redirect('/event/waitlist/%s' % token, message=_("You have left the waitlist."))

    # ------------------------------------------------------------
    # Booking self-service
    # ------------------------------------------------------------

    @http.route('/event/booking/<string:token>', type='http', auth='public', website=True, sitemap=False)
    def booking_page(self, token, error=None, message=None, **kw):
        registration = self._registration(token)
        if not registration:
            return request.render('conference_registration.booking_not_found', {})
        return request.render('conference_registration.booking_page', {
            'registration': registration,
            'event': registration.event_id,
            'countries': request.env['res.country'].sudo().search([]),
            'error': error,
            'message': message,
        })

    @http.route('/event/booking/<string:token>/transfer', type='http', auth='public', website=True,
                sitemap=False, methods=['POST'])
    def booking_transfer(self, token, name=None, email=None, phone=None, **kw):
        registration = self._registration(token)
        if not registration:
            raise request.not_found()
        try:
            with request.env.cr.savepoint():
                registration._conference_transfer(name, email, phone)
        except (UserError, ValidationError) as e:
            return _redirect('/event/booking/%s' % token, error=str(e))
        # The old link no longer works: show a neutral confirmation page.
        return request.render('conference_registration.booking_transferred', {
            'event': registration.event_id, 'new_name': registration.name,
        })

    @http.route('/event/booking/<string:token>/cancel', type='http', auth='public', website=True,
                sitemap=False, methods=['POST'])
    def booking_cancel(self, token, **kw):
        registration = self._registration(token)
        if not registration:
            raise request.not_found()
        try:
            with request.env.cr.savepoint():
                registration._conference_self_cancel()
        except (UserError, ValidationError) as e:
            return _redirect('/event/booking/%s' % token, error=str(e))
        return _redirect('/event/booking/%s' % token, message=_("Your booking is cancelled."))

    @http.route('/event/booking/<string:token>/visa', type='http', auth='public', website=True,
                sitemap=False, methods=['POST'])
    def booking_visa_request(self, token, **post):
        registration = self._registration(token)
        if not registration:
            raise request.not_found()
        country = request.env['res.country'].sudo().browse(int(post.get('visa_nationality_id') or 0)).exists()
        vals = {
            'visa_full_name': post.get('visa_full_name'),
            'visa_passport_number': post.get('visa_passport_number'),
            'visa_nationality_id': country.id,
            'visa_date_of_birth': post.get('visa_date_of_birth'),
            'visa_passport_expiry': post.get('visa_passport_expiry'),
        }
        try:
            with request.env.cr.savepoint():
                for key in ('visa_date_of_birth', 'visa_passport_expiry'):
                    if vals[key]:
                        try:
                            vals[key] = fields.Date.to_date(vals[key])
                        except ValueError:
                            raise UserError(_("Please enter dates as YYYY-MM-DD."))
                registration._conference_visa_request(vals)
        except (UserError, ValidationError) as e:
            return _redirect('/event/booking/%s' % token, error=str(e))
        msg = (_("Your letter is ready. We have also emailed it to you.")
               if registration.visa_letter_state == 'issued'
               else _("Thank you. The organisers will review your request and email you the letter."))
        return _redirect('/event/booking/%s' % token, message=msg)

    @http.route('/event/booking/<string:token>/visa-letter', type='http', auth='public', sitemap=False)
    def booking_visa_letter(self, token, **kw):
        registration = self._registration(token)
        if not registration or registration.visa_letter_state != 'issued':
            raise request.not_found()
        pdf, _type = request.env['ir.actions.report'].sudo()._render_qweb_pdf(
            'conference_registration.action_report_visa_letter', registration.ids)
        filename = 'Invitation-letter-%s.pdf' % registration.visa_letter_reference
        return request.make_response(pdf, headers=[
            ('Content-Type', 'application/pdf'),
            ('Content-Length', str(len(pdf))),
            ('Content-Disposition', http.content_disposition(filename)),
        ])

    @http.route('/event/visa-letter/verify/<string:reference>/<string:code>', type='http', auth='public',
                website=True, sitemap=False)
    def visa_letter_verify(self, reference, code, **kw):
        registration = request.env['event.registration'].sudo().search([
            ('visa_letter_reference', '=', reference), ('visa_letter_state', '=', 'issued')], limit=1)
        valid = bool(registration) and registration._visa_verification_code() == code \
            and registration.state in ('open', 'done')
        return request.render('conference_registration.visa_verify_page', {
            'valid': valid,
            'registration': registration if valid else None,
            'reference': reference,
        })


class ConferenceTicketsPortal(CustomerPortal):

    def _conference_ticket_domain(self):
        partner = request.env.user.partner_id
        domain = [('partner_id', '=', partner.id)]
        if partner.email:
            domain = ['|', ('email', '=ilike', partner.email)] + domain
        return domain

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        if 'conference_ticket_count' in counters:
            values['conference_ticket_count'] = request.env['event.registration'].sudo().search_count(
                self._conference_ticket_domain())
        return values

    @http.route(['/my/tickets', '/my/tickets/page/<int:page>'], type='http', auth='user', website=True)
    def portal_my_tickets(self, page=1, **kw):
        Registration = request.env['event.registration'].sudo()
        domain = self._conference_ticket_domain()
        total = Registration.search_count(domain)
        pager = portal_pager(url='/my/tickets', total=total, page=page, step=self._items_per_page)
        registrations = Registration.search(domain, order='create_date desc',
                                            limit=self._items_per_page, offset=pager['offset'])
        values = self._prepare_portal_layout_values()
        values.update({'registrations': registrations, 'pager': pager, 'page_name': 'conference_tickets'})
        return request.render('conference_registration.portal_my_tickets', values)

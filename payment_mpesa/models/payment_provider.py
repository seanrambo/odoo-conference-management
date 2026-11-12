import base64
import re
import time

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from odoo.addons.payment_mpesa import const

# Daraja OAuth tokens live for an hour; keep them per database/provider/mode.
_TOKEN_CACHE = {}
_TOKEN_MARGIN = 120  # seconds of safety before expiry


class PaymentProvider(models.Model):
    _inherit = 'payment.provider'

    code = fields.Selection(selection_add=[('mpesa', "M-Pesa")], ondelete={'mpesa': 'set default'})
    mpesa_consumer_key = fields.Char(
        string="Consumer Key", copy=False, groups='base.group_system',
        help="From your app on the Safaricom Daraja portal.")
    mpesa_consumer_secret = fields.Char(string="Consumer Secret", copy=False, groups='base.group_system')
    mpesa_shortcode = fields.Char(
        string="Business Short Code", copy=False,
        help="Your Paybill number, or for a Till the Head Office (store) number used with the passkey.")
    mpesa_passkey = fields.Char(string="Passkey", copy=False, groups='base.group_system')
    mpesa_transaction_type = fields.Selection([
        ('CustomerPayBillOnline', "Paybill"),
        ('CustomerBuyGoodsOnline', "Till (Buy Goods)"),
    ], string="Account Type", default='CustomerPayBillOnline')
    mpesa_till_number = fields.Char(
        string="Till Number", copy=False,
        help="Till accounts only: the till that receives the money. Paybill accounts leave this empty.")

    # === COMPUTE / CRUD === #

    def _get_supported_currencies(self):
        supported = super()._get_supported_currencies()
        if self.code == 'mpesa':
            supported = supported.filtered(lambda c: c.name in const.SUPPORTED_CURRENCIES)
        return supported

    def _get_default_payment_method_codes(self):
        self.ensure_one()
        if self.code != 'mpesa':
            return super()._get_default_payment_method_codes()
        return const.DEFAULT_PAYMENT_METHOD_CODES

    @api.constrains('state', 'code', 'mpesa_shortcode', 'mpesa_transaction_type', 'mpesa_till_number')
    def _check_mpesa_configuration(self):
        for provider in self.sudo().filtered(lambda p: p.code == 'mpesa' and p.state != 'disabled'):
            missing = [
                label for label, value in (
                    (_("Consumer Key"), provider.mpesa_consumer_key),
                    (_("Consumer Secret"), provider.mpesa_consumer_secret),
                    (_("Business Short Code"), provider.mpesa_shortcode),
                    (_("Passkey"), provider.mpesa_passkey),
                ) if not value
            ]
            if provider.mpesa_transaction_type == 'CustomerBuyGoodsOnline' and not provider.mpesa_till_number:
                missing.append(_("Till Number"))
            if missing:
                raise ValidationError(_(
                    "Fill in these M-Pesa settings before enabling the provider: %s", ', '.join(missing)))

    # === API HELPERS === #

    def _mpesa_base_url(self):
        return const.API_URLS['enabled' if self.state == 'enabled' else 'test']

    def _build_request_url(self, endpoint, **kwargs):
        if self.code != 'mpesa':
            return super()._build_request_url(endpoint, **kwargs)
        return self._mpesa_base_url() + endpoint

    def _build_request_headers(self, method, endpoint, payload, **kwargs):
        if self.code != 'mpesa':
            return super()._build_request_headers(method, endpoint, payload, **kwargs)
        if endpoint == const.OAUTH_ENDPOINT:
            return {}
        return {'Authorization': 'Bearer %s' % self._mpesa_get_access_token()}

    def _build_request_auth(self, **kwargs):
        if self.code != 'mpesa' or not kwargs.get('mpesa_basic_auth'):
            return super()._build_request_auth(**kwargs)
        sudo = self.sudo()
        return (sudo.mpesa_consumer_key, sudo.mpesa_consumer_secret)

    def _parse_response_error(self, response):
        if self.code != 'mpesa':
            return super()._parse_response_error(response)
        try:
            content = response.json()
        except ValueError:
            return response.text
        return content.get('errorMessage') or content.get('ResponseDescription') or response.text

    def _mpesa_get_access_token(self):
        self.ensure_one()
        key = (self.env.cr.dbname, self.id, self.state)
        cached = _TOKEN_CACHE.get(key)
        if cached and cached[1] > time.time():
            return cached[0]
        content = self._send_api_request(
            'GET', const.OAUTH_ENDPOINT, params={'grant_type': 'client_credentials'},
            mpesa_basic_auth=True)
        token = content.get('access_token')
        if not token:
            raise ValidationError(_("M-Pesa did not return an access token. Check the consumer key and secret."))
        ttl = int(content.get('expires_in') or 3599)
        _TOKEN_CACHE[key] = (token, time.time() + ttl - _TOKEN_MARGIN)
        return token

    def _mpesa_password(self, timestamp):
        sudo = self.sudo()
        raw = '%s%s%s' % (sudo.mpesa_shortcode, sudo.mpesa_passkey, timestamp)
        return base64.b64encode(raw.encode()).decode()

    def _mpesa_party_b(self):
        if self.mpesa_transaction_type == 'CustomerBuyGoodsOnline':
            return self.mpesa_till_number
        return self.mpesa_shortcode

    @api.model
    def _mpesa_normalize_phone(self, phone):
        """Return the phone number in the 2547XXXXXXXX / 2541XXXXXXXX format
        Daraja expects, or raise a ValidationError the customer can act on."""
        digits = re.sub(r'\D', '', phone or '')
        if digits.startswith('0') and len(digits) == 10:
            digits = '254' + digits[1:]
        elif len(digits) == 9 and digits[0] in '71':
            digits = '254' + digits
        if not re.fullmatch(r'254[71]\d{8}', digits):
            raise ValidationError(_(
                "Enter a Safaricom number such as 0712 345 678 or +254 712 345 678."))
        return digits

import logging
import math
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from odoo.addons.payment import utils as payment_utils
from odoo.addons.payment_mpesa import const

_logger = logging.getLogger(__name__)


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    mpesa_phone = fields.Char("M-Pesa Phone", readonly=True, copy=False)
    mpesa_receipt = fields.Char("M-Pesa Receipt", readonly=True, copy=False, index='btree_not_null')

    # === PROCESSING VALUES === #

    def _get_specific_processing_values(self, processing_values):
        res = super()._get_specific_processing_values(processing_values)
        if self.provider_code != 'mpesa':
            return res
        return {'mpesa_access_token': payment_utils.generate_access_token(self.reference, env=self.env)}

    def _mpesa_amount(self):
        """M-Pesa only moves whole shillings. Round down so the customer is
        never charged more than the order; the core amount check is also
        done at zero decimals (see `_extract_amount_data`)."""
        return int(math.floor(self.amount))

    # === STK PUSH === #

    def _mpesa_send_stk_push(self, phone):
        """Ask Safaricom to prompt ``phone`` for the transaction amount."""
        self.ensure_one()
        provider = self.provider_id
        msisdn = provider._mpesa_normalize_phone(phone)  # raises a message for the customer
        if self.currency_id.name not in const.SUPPORTED_CURRENCIES:
            self._set_error(_("M-Pesa only accepts payments in Kenya shillings (KES)."))
            return
        amount = self._mpesa_amount()
        if amount < 1:
            self._set_error(_("The amount is too small for an M-Pesa payment."))
            return

        timestamp = fields.Datetime.context_timestamp(
            self.with_context(tz='Africa/Nairobi'), fields.Datetime.now()).strftime('%Y%m%d%H%M%S')
        payload = {
            'BusinessShortCode': provider.mpesa_shortcode,
            'Password': provider._mpesa_password(timestamp),
            'Timestamp': timestamp,
            'TransactionType': provider.mpesa_transaction_type,
            'Amount': amount,
            'PartyA': msisdn,
            'PartyB': provider._mpesa_party_b(),
            'PhoneNumber': msisdn,
            'CallBackURL': '%s/payment/mpesa/callback' % provider.get_base_url(),
            'AccountReference': self.reference[:const.ACCOUNT_REFERENCE_MAX],
            'TransactionDesc': (self.company_id.name or 'Payment')[:const.TRANSACTION_DESC_MAX],
        }
        try:
            content = self._send_api_request('POST', const.STK_PUSH_ENDPOINT, json=payload)
        except ValidationError as e:
            self._set_error(str(e))
            return
        if str(content.get('ResponseCode')) != '0' or not content.get('CheckoutRequestID'):
            self._set_error(content.get('errorMessage') or content.get('ResponseDescription')
                            or _("M-Pesa could not start the payment."))
            return
        self.write({'provider_reference': content['CheckoutRequestID'], 'mpesa_phone': msisdn})
        self._set_pending(state_message=_(
            "We sent a payment request to %s. Enter your M-Pesa PIN on your phone to complete it.",
            msisdn))

    def _mpesa_query_status(self, receipt=None):
        """Confirm the result of the STK push with Safaricom and update the
        transaction. Callbacks are unauthenticated, so this query is the only
        source we trust for the final state."""
        self.ensure_one()
        if self.provider_code != 'mpesa' or self.state not in ('draft', 'pending') or not self.provider_reference:
            return
        provider = self.provider_id
        timestamp = fields.Datetime.context_timestamp(
            self.with_context(tz='Africa/Nairobi'), fields.Datetime.now()).strftime('%Y%m%d%H%M%S')
        payload = {
            'BusinessShortCode': provider.mpesa_shortcode,
            'Password': provider._mpesa_password(timestamp),
            'Timestamp': timestamp,
            'CheckoutRequestID': self.provider_reference,
        }
        try:
            content = self._send_api_request('POST', const.STK_QUERY_ENDPOINT, json=payload)
        except ValidationError as e:
            # Daraja answers "still processing" with an HTTP error; keep waiting.
            if const.QUERY_STILL_PROCESSING in str(e) or 'being processed' in str(e).lower():
                return
            _logger.warning("M-Pesa status query failed for %s: %s", self.reference, e)
            return
        if content.get('errorCode') == const.QUERY_STILL_PROCESSING or 'ResultCode' not in content:
            return
        self._process('mpesa', {
            'reference': self.reference,
            'result_code': str(content['ResultCode']),
            'result_desc': content.get('ResultDesc'),
            'receipt': receipt,
        })

    # === PAYMENT DATA PROCESSING === #

    def _extract_amount_data(self, payment_data):
        if self.provider_code != 'mpesa':
            return super()._extract_amount_data(payment_data)
        # The amount is fixed by us in the STK push and the result is confirmed
        # by Safaricom's query (which does not echo it), so no amount to compare.
        return None

    def _apply_updates(self, payment_data):
        if self.provider_code != 'mpesa':
            return super()._apply_updates(payment_data)
        code = payment_data.get('result_code')
        if code == const.RESULT_SUCCESS:
            if payment_data.get('receipt'):
                self.mpesa_receipt = payment_data['receipt']
            self._set_done()
        elif code in const.RESULT_CANCELLED:
            self._set_canceled(state_message=const.RESULT_CANCELLED[code])
        else:
            self._set_error(_("M-Pesa declined the payment: %s",
                              payment_data.get('result_desc') or code))

    # === CRON === #

    @api.model
    def _cron_mpesa_check_pending(self):
        """Settle M-Pesa payments whose callback never arrived (for example
        when the server is not reachable from the internet)."""
        since = fields.Datetime.now() - timedelta(days=1)
        pending = self.search([
            ('provider_code', '=', 'mpesa'), ('state', '=', 'pending'),
            ('provider_reference', '!=', False), ('create_date', '>=', since),
        ], limit=200)
        for tx in pending:
            try:
                with self.env.cr.savepoint():
                    tx._mpesa_query_status()
            except Exception:
                _logger.exception("M-Pesa: could not check %s", tx.reference)
        if pending:
            self.env.ref('payment.cron_post_process_payment_tx')._trigger()

    def action_mpesa_check_status(self):
        for tx in self.filtered(lambda t: t.provider_code == 'mpesa'):
            tx._mpesa_query_status()
        return True

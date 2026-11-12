import logging

from werkzeug.exceptions import Forbidden

from odoo import _, http
from odoo.exceptions import ValidationError
from odoo.http import request

from odoo.addons.payment import utils as payment_utils

_logger = logging.getLogger(__name__)


class MpesaController(http.Controller):
    _stk_push_url = '/payment/mpesa/stk_push'
    _callback_url = '/payment/mpesa/callback'

    @http.route(_stk_push_url, type='jsonrpc', auth='public')
    def mpesa_stk_push(self, reference, access_token, phone):
        """Send the STK push for a transaction created by the payment form."""
        tx = request.env['payment.transaction'].sudo().search(
            [('reference', '=', reference), ('provider_code', '=', 'mpesa')], limit=1)
        if not tx or not payment_utils.check_access_token(access_token, reference):
            raise Forbidden()
        if tx.state != 'draft':
            raise ValidationError(_("This payment has already been started. Please refresh the page."))
        tx._mpesa_send_stk_push(phone)
        if tx.state == 'error':
            raise ValidationError(tx.state_message)
        return {'state': tx.state}

    @http.route(_callback_url, type='http', auth='public', methods=['POST'], csrf=False, save_session=False)
    def mpesa_callback(self, **kwargs):
        """Safaricom posts the STK result here. The body is not signed, so it
        only tells us *which* transaction to check; the outcome comes from
        Safaricom's query API."""
        try:
            data = request.get_json_data()
            callback = data['Body']['stkCallback']
            checkout_id = callback['CheckoutRequestID']
        except Exception:
            _logger.warning("M-Pesa: ignored malformed callback")
            return request.make_json_response({'ResultCode': 0, 'ResultDesc': 'Accepted'})

        receipt = None
        for item in (callback.get('CallbackMetadata') or {}).get('Item', []):
            if item.get('Name') == 'MpesaReceiptNumber':
                receipt = str(item.get('Value') or '')[:32] or None
        tx = request.env['payment.transaction'].sudo().search([
            ('provider_code', '=', 'mpesa'), ('provider_reference', '=', checkout_id),
        ], limit=1)
        if tx:
            tx._mpesa_query_status(receipt=receipt)
        else:
            _logger.warning("M-Pesa: callback for unknown request %s", checkout_id)
        return request.make_json_response({'ResultCode': 0, 'ResultDesc': 'Accepted'})

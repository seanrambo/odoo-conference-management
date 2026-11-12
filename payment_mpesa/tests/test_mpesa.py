import json
from contextlib import contextmanager
from unittest.mock import patch

from odoo.exceptions import ValidationError
from odoo.tests import HttpCase, TransactionCase, tagged

from odoo.addons.payment import utils as payment_utils
from odoo.addons.payment_mpesa import const


class MpesaCommon:

    @classmethod
    def _setup_mpesa(cls):
        cls.kes = cls.env.ref('base.KES')
        cls.kes.active = True
        cls.provider = cls.env.ref('payment_mpesa.payment_provider_mpesa')
        cls.provider.write({
            'mpesa_consumer_key': 'key', 'mpesa_consumer_secret': 'secret',
            'mpesa_shortcode': '174379', 'mpesa_passkey': 'passkey',
            'mpesa_transaction_type': 'CustomerPayBillOnline',
            'state': 'test', 'is_published': True,
        })
        cls.partner = cls.env['res.partner'].create({'name': 'Amina', 'email': 'amina@example.com'})

    def _tx(self, amount=1500.75, reference='MP-TEST-1'):
        return self.env['payment.transaction'].create({
            'provider_id': self.provider.id,
            'payment_method_id': self.env.ref('payment.payment_method_mpesa').id,
            'amount': amount,
            'currency_id': self.kes.id,
            'partner_id': self.partner.id,
            'reference': reference,
        })

    @contextmanager
    def _mock_api(self, responses):
        """``responses`` maps an endpoint to a dict, or to an exception to raise."""
        calls = []

        def fake(provider, method, endpoint, **kwargs):
            calls.append((endpoint, kwargs.get('json')))
            result = responses[endpoint]
            if isinstance(result, Exception):
                raise result
            return result

        with patch.object(type(self.env['payment.provider']), '_send_api_request', fake):
            yield calls


@tagged('post_install', '-at_install', 'mpesa')
class TestMpesa(MpesaCommon, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_mpesa()

    def test_phone_normalisation(self):
        norm = self.env['payment.provider']._mpesa_normalize_phone
        self.assertEqual(norm('0712 345 678'), '254712345678')
        self.assertEqual(norm('+254 712-345-678'), '254712345678')
        self.assertEqual(norm('712345678'), '254712345678')
        self.assertEqual(norm('0110345678'), '254110345678')
        for bad in ('', '12345', '0812345678', '+255712345678'):
            with self.assertRaises(ValidationError):
                norm(bad)

    def test_stk_push_sets_pending(self):
        tx = self._tx()
        with self._mock_api({const.STK_PUSH_ENDPOINT: {'ResponseCode': '0', 'CheckoutRequestID': 'ws_CO_1'}}) as calls:
            tx._mpesa_send_stk_push('0712345678')
        self.assertEqual(tx.state, 'pending')
        self.assertEqual(tx.provider_reference, 'ws_CO_1')
        self.assertEqual(tx.mpesa_phone, '254712345678')
        payload = calls[0][1]
        self.assertEqual(payload['Amount'], 1500, "M-Pesa takes whole shillings, rounded down")
        self.assertEqual(payload['PartyB'], '174379')
        self.assertTrue(payload['CallBackURL'].endswith('/payment/mpesa/callback'))
        self.assertLessEqual(len(payload['AccountReference']), 12)

    def test_till_uses_till_number(self):
        self.provider.write({'mpesa_transaction_type': 'CustomerBuyGoodsOnline', 'mpesa_till_number': '600100'})
        tx = self._tx()
        with self._mock_api({const.STK_PUSH_ENDPOINT: {'ResponseCode': '0', 'CheckoutRequestID': 'ws_CO_2'}}) as calls:
            tx._mpesa_send_stk_push('0712345678')
        self.assertEqual(calls[0][1]['PartyB'], '600100')
        self.assertEqual(calls[0][1]['TransactionType'], 'CustomerBuyGoodsOnline')

    def test_stk_push_rejected(self):
        tx = self._tx()
        with self._mock_api({const.STK_PUSH_ENDPOINT: ValidationError('Invalid Access Token')}):
            tx._mpesa_send_stk_push('0712345678')
        self.assertEqual(tx.state, 'error')

    def test_non_kes_is_refused(self):
        usd = self.env.ref('base.USD')
        tx = self._tx()
        tx.currency_id = usd
        with self._mock_api({}):
            tx._mpesa_send_stk_push('0712345678')
        self.assertEqual(tx.state, 'error')

    def _pending_tx(self):
        tx = self._tx()
        with self._mock_api({const.STK_PUSH_ENDPOINT: {'ResponseCode': '0', 'CheckoutRequestID': 'ws_CO_9'}}):
            tx._mpesa_send_stk_push('0712345678')
        return tx

    def test_query_success_with_receipt(self):
        tx = self._pending_tx()
        with self._mock_api({const.STK_QUERY_ENDPOINT: {'ResultCode': '0', 'ResultDesc': 'ok'}}):
            tx._mpesa_query_status(receipt='SGH7XYZ12')
        self.assertEqual(tx.state, 'done')
        self.assertEqual(tx.mpesa_receipt, 'SGH7XYZ12')

    def test_query_cancelled_and_failed(self):
        tx = self._pending_tx()
        with self._mock_api({const.STK_QUERY_ENDPOINT: {'ResultCode': '1032', 'ResultDesc': 'Cancelled'}}):
            tx._mpesa_query_status()
        self.assertEqual(tx.state, 'cancel')

        tx2 = self._tx(reference='MP-TEST-2')
        with self._mock_api({const.STK_PUSH_ENDPOINT: {'ResponseCode': '0', 'CheckoutRequestID': 'ws_CO_10'}}):
            tx2._mpesa_send_stk_push('0712345678')
        with self._mock_api({const.STK_QUERY_ENDPOINT: {'ResultCode': '1', 'ResultDesc': 'Insufficient balance'}}):
            tx2._mpesa_query_status()
        self.assertEqual(tx2.state, 'error')
        self.assertIn('Insufficient balance', tx2.state_message)

    def test_query_still_processing_keeps_pending(self):
        tx = self._pending_tx()
        with self._mock_api({const.STK_QUERY_ENDPOINT: ValidationError(
                'The payment provider rejected the request.\nThe transaction is being processed')}):
            tx._mpesa_query_status()
        self.assertEqual(tx.state, 'pending')
        with self._mock_api({const.STK_QUERY_ENDPOINT: {'errorCode': const.QUERY_STILL_PROCESSING}}):
            tx._mpesa_query_status()
        self.assertEqual(tx.state, 'pending')

    def test_cron_settles_pending(self):
        tx = self._pending_tx()
        with self._mock_api({const.STK_QUERY_ENDPOINT: {'ResultCode': '0', 'ResultDesc': 'ok'}}):
            self.env['payment.transaction']._cron_mpesa_check_pending()
        self.assertEqual(tx.state, 'done')

    def test_cannot_enable_without_credentials(self):
        with self.assertRaises(ValidationError):
            self.provider.copy({'mpesa_passkey': False, 'state': 'test'})


@tagged('post_install', '-at_install', 'mpesa')
class TestMpesaRoutes(MpesaCommon, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_mpesa()

    def _rpc(self, url, params):
        return self.url_open(url, data=json.dumps({'jsonrpc': '2.0', 'method': 'call', 'params': params}),
                             headers={'Content-Type': 'application/json'}).json()

    def test_stk_push_route_needs_valid_token(self):
        tx = self._tx(reference='MP-ROUTE-1')
        with self._mock_api({const.STK_PUSH_ENDPOINT: {'ResponseCode': '0', 'CheckoutRequestID': 'ws_CO_R1'}}):
            res = self._rpc('/payment/mpesa/stk_push', {'reference': tx.reference, 'access_token': 'wrong', 'phone': '0712345678'})
            self.assertIn('error', res)
            self.assertEqual(tx.state, 'draft')
            token = payment_utils.generate_access_token(tx.reference, env=self.env)
            res = self._rpc('/payment/mpesa/stk_push', {'reference': tx.reference, 'access_token': token, 'phone': '0712345678'})
        self.assertEqual(res.get('result'), {'state': 'pending'})
        self.assertEqual(tx.provider_reference, 'ws_CO_R1')

    def test_forged_callback_cannot_complete_payment(self):
        tx = self._tx(reference='MP-ROUTE-2')
        with self._mock_api({const.STK_PUSH_ENDPOINT: {'ResponseCode': '0', 'CheckoutRequestID': 'ws_CO_R2'}}):
            tx._mpesa_send_stk_push('0712345678')
        forged = {'Body': {'stkCallback': {
            'MerchantRequestID': 'x', 'CheckoutRequestID': 'ws_CO_R2', 'ResultCode': 0, 'ResultDesc': 'Success',
            'CallbackMetadata': {'Item': [{'Name': 'MpesaReceiptNumber', 'Value': 'FAKE123'}]},
        }}}
        # Safaricom's own record says the customer cancelled.
        with self._mock_api({const.STK_QUERY_ENDPOINT: {'ResultCode': '1032', 'ResultDesc': 'Cancelled'}}):
            res = self.url_open('/payment/mpesa/callback', data=json.dumps(forged),
                                headers={'Content-Type': 'application/json'})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(tx.state, 'cancel')
        self.assertFalse(tx.mpesa_receipt)

    def test_genuine_callback_completes_payment(self):
        tx = self._tx(reference='MP-ROUTE-3')
        with self._mock_api({const.STK_PUSH_ENDPOINT: {'ResponseCode': '0', 'CheckoutRequestID': 'ws_CO_R3'}}):
            tx._mpesa_send_stk_push('0712345678')
        body = {'Body': {'stkCallback': {
            'CheckoutRequestID': 'ws_CO_R3', 'ResultCode': 0,
            'CallbackMetadata': {'Item': [{'Name': 'Amount', 'Value': 1500}, {'Name': 'MpesaReceiptNumber', 'Value': 'SGH7REAL1'}]},
        }}}
        with self._mock_api({const.STK_QUERY_ENDPOINT: {'ResultCode': '0', 'ResultDesc': 'ok'}}):
            self.url_open('/payment/mpesa/callback', data=json.dumps(body), headers={'Content-Type': 'application/json'})
        self.assertEqual(tx.state, 'done')
        self.assertEqual(tx.mpesa_receipt, 'SGH7REAL1')

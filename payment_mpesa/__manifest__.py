{
    'name': 'Payment Provider: M-Pesa',
    'version': '19.0.1.0.0',
    'category': 'Accounting/Payment Providers',
    'sequence': 350,
    'summary': "Accept M-Pesa payments online with Safaricom Daraja STK push.",
    'description': """
M-Pesa (Safaricom Daraja)
=========================

Customers enter their phone number at checkout, receive a payment prompt on
their phone and approve it with their M-Pesa PIN.

* Paybill and Till (Buy Goods) accounts.
* Sandbox in test mode, production when enabled.
* Every result is confirmed with Safaricom's STK query before the payment is
  marked as done, so a forged callback cannot complete an order.
* A scheduled job checks pending payments in case a callback never arrives.
""",
    'author': 'R4M80',
    'license': 'LGPL-3',
    'depends': ['payment'],
    'data': [
        'views/payment_mpesa_templates.xml',
        'views/payment_provider_views.xml',
        'views/payment_transaction_views.xml',
        'data/payment_provider_data.xml',
        'data/ir_cron_data.xml',
    ],
    'assets': {
        'web.assets_frontend': [
            'payment_mpesa/static/src/interactions/**/*',
        ],
    },
    'post_init_hook': 'post_init_hook',
    'uninstall_hook': 'uninstall_hook',
    'installable': True,
}

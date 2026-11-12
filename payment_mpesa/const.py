API_URLS = {
    'test': 'https://sandbox.safaricom.co.ke',
    'enabled': 'https://api.safaricom.co.ke',
}

OAUTH_ENDPOINT = '/oauth/v1/generate'
STK_PUSH_ENDPOINT = '/mpesa/stkpush/v1/processrequest'
STK_QUERY_ENDPOINT = '/mpesa/stkpushquery/v1/query'

SUPPORTED_CURRENCIES = ('KES',)
DEFAULT_PAYMENT_METHOD_CODES = {'mpesa'}

# STK result codes, see Daraja documentation.
RESULT_SUCCESS = '0'
RESULT_CANCELLED = {
    '1032': "You cancelled the M-Pesa request on your phone.",
    '1037': "The M-Pesa request timed out before it was answered on the phone.",
    '1019': "The M-Pesa request expired before it was completed.",
    '1025': "The M-Pesa prompt could not be sent to the phone. Please try again.",
}
# Returned by the query endpoint while the customer has not answered yet.
QUERY_STILL_PROCESSING = '500.001.1001'

ACCOUNT_REFERENCE_MAX = 12
TRANSACTION_DESC_MAX = 13

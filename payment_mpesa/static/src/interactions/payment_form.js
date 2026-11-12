import { patch } from '@web/core/utils/patch';
import { _t } from '@web/core/l10n/translation';
import { rpc, RPCError } from '@web/core/network/rpc';

import { PaymentForm } from '@payment/interactions/payment_form';

patch(PaymentForm.prototype, {

    async _prepareInlineForm(providerId, providerCode, paymentOptionId, paymentMethodCode, flow) {
        if (providerCode !== 'mpesa') {
            await super._prepareInlineForm(...arguments);
            return;
        }
        this._setPaymentFlow('direct');
    },

    async _processDirectFlow(providerCode, paymentOptionId, paymentMethodCode, processingValues) {
        if (providerCode !== 'mpesa') {
            await super._processDirectFlow(...arguments);
            return;
        }
        const container = document.getElementById(`o_mpesa_container_${processingValues.provider_id}`);
        const phone = container?.querySelector('.o_mpesa_phone')?.value || '';
        try {
            await this.waitFor(rpc('/payment/mpesa/stk_push', {
                reference: processingValues.reference,
                access_token: processingValues.mpesa_access_token,
                phone: phone,
            }));
            window.location = '/payment/status';
        } catch (error) {
            if (error instanceof RPCError) {
                this._displayErrorDialog(_t("M-Pesa payment could not start"), error.data.message);
                this._enableButton();
            } else {
                return Promise.reject(error);
            }
        }
    },
});

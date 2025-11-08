const formSelectors = ['[data-purchase-order-form]', '[data-purchase-payment-form]'];
const orderForms = document.querySelectorAll(formSelectors.join(','));

orderForms.forEach((root) => {
  const deliverySelect = root.querySelector('[data-delivery-select]');
  const shippingFields = root.querySelector('[data-shipping-fields]');
  const paymentSelect = root.querySelector('[data-payment-method]');
  const bankSection = root.querySelector('[data-bank-section]');
  const paymentAmountInput = root.querySelector('[data-payment-amount-input]');
  const confirmPaymentButton = root.querySelector('[data-confirm-payment-button]');
  const reopenPaymentButton = root.querySelector('[data-reopen-payment-button]');
  const paymentAmountAlert = root.querySelector('[data-payment-amount-alert]');

  function toggleSection(section, visible) {
    if (!section) {
      return;
    }
    section.hidden = !visible;
    section.classList.toggle('hidden', !visible);
    section.setAttribute('aria-hidden', visible ? 'false' : 'true');
  }

  const shippingInputs = shippingFields ? Array.from(shippingFields.querySelectorAll('input, textarea, select')) : [];
  const bankInputs = bankSection ? Array.from(bankSection.querySelectorAll('input, textarea, select')) : [];

  function syncDelivery() {
    if (!deliverySelect || !shippingFields) {
      return;
    }
    const visible = deliverySelect.value === 'shipping';
    toggleSection(shippingFields, visible);
    shippingInputs.forEach((input) => {
      input.disabled = !visible;
    });
  }

  function syncPayment() {
    if (!paymentSelect || !bankSection) {
      return;
    }
    const value = paymentSelect.value;
    const visible = value === 'transferencia';
    toggleSection(bankSection, visible);
    bankInputs.forEach((input) => {
      input.disabled = !visible;
    });
  }

  function syncPaymentAmountWarning() {
    if (!paymentAmountInput) {
      return;
    }
    const estimated = parseFloat(paymentAmountInput.dataset.estimatedTotal || '0');
    const entered = parseFloat(paymentAmountInput.value);
    const normalizedEstimated = Number.isNaN(estimated) ? 0 : estimated;
    const normalizedEntered = Number.isNaN(entered) ? 0 : entered;
    const exceeds = normalizedEntered > normalizedEstimated;
    if (confirmPaymentButton) {
      confirmPaymentButton.hidden = exceeds;
      confirmPaymentButton.classList.toggle('hidden', exceeds);
    }
    if (reopenPaymentButton) {
      reopenPaymentButton.hidden = !exceeds;
      reopenPaymentButton.classList.toggle('hidden', !exceeds);
    }
    if (paymentAmountAlert) {
      paymentAmountAlert.hidden = !exceeds;
      paymentAmountAlert.classList.toggle('hidden', !exceeds);
    }
  }

  if (deliverySelect) {
    deliverySelect.addEventListener('change', syncDelivery);
    deliverySelect.addEventListener('input', syncDelivery);
  }
  if (paymentSelect) {
    paymentSelect.addEventListener('change', syncPayment);
  }
  if (paymentAmountInput) {
    paymentAmountInput.addEventListener('input', syncPaymentAmountWarning);
    paymentAmountInput.addEventListener('change', syncPaymentAmountWarning);
  }

  syncDelivery();
  syncPayment();
  syncPaymentAmountWarning();
});

const formSelectors = ['[data-purchase-order-form]', '[data-purchase-payment-form]'];
const orderForms = document.querySelectorAll(formSelectors.join(','));

orderForms.forEach((root) => {
  const deliverySelect = root.querySelector('[data-delivery-select]');
  const shippingFields = root.querySelector('[data-shipping-fields]');
  const paymentSelect = root.querySelector('[data-payment-method]');
  const bankSection = root.querySelector('[data-bank-section]');

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

  if (deliverySelect) {
    deliverySelect.addEventListener('change', syncDelivery);
    deliverySelect.addEventListener('input', syncDelivery);
  }
  if (paymentSelect) {
    paymentSelect.addEventListener('change', syncPayment);
  }

  syncDelivery();
  syncPayment();
});

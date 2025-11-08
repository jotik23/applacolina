const highlightClasses = ['bg-amber-50/60', 'ring-1', 'ring-amber-200'];

function updateRowState(row) {
  const requested = Number(row.dataset.requested || 0);
  const input = row.querySelector('input[name*="[received_quantity]"]');
  const mismatchBadge = row.querySelector('[data-reception-row-alert]');
  if (!input) {
    highlightClasses.forEach((cls) => row.classList.remove(cls));
    if (mismatchBadge) {
      mismatchBadge.hidden = true;
    }
    return false;
  }
  const received = parseFloat(input.value);
  const normalizedReceived = Number.isNaN(received) ? 0 : received;
  const mismatch = Math.abs(requested - normalizedReceived) > 0.0001;
  highlightClasses.forEach((cls) => row.classList.toggle(cls, mismatch));
  if (mismatchBadge) {
    mismatchBadge.hidden = !mismatch;
  }
  return mismatch;
}

function initReceptionForms() {
  const receptionForms = document.querySelectorAll('[data-purchase-reception-form]');
  receptionForms.forEach((root) => {
    const rows = Array.from(root.querySelectorAll('[data-reception-row]'));
    const mismatchAlert = root.querySelector('[data-reception-mismatch-alert]');

    function refreshFormState() {
      let hasMismatch = false;
      rows.forEach((row) => {
        const rowMismatch = updateRowState(row);
        hasMismatch = hasMismatch || rowMismatch;
      });
      if (mismatchAlert) {
        mismatchAlert.hidden = !hasMismatch;
      }
    }

    rows.forEach((row) => {
      const input = row.querySelector('input[name*="[received_quantity]"]');
      if (!input) {
        return;
      }
      input.addEventListener('input', refreshFormState);
      input.addEventListener('change', refreshFormState);
    });

    refreshFormState();
  });
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initReceptionForms);
} else {
  initReceptionForms();
}

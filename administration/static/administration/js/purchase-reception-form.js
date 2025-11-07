const receptionForms = document.querySelectorAll('[data-purchase-reception-form]');

function formatNumber(value) {
  const number = Number(value);
  if (Number.isNaN(number)) {
    return '0';
  }
  return number.toLocaleString('es-CO', { minimumFractionDigits: number % 1 === 0 ? 0 : 2, maximumFractionDigits: 2 });
}

receptionForms.forEach((root) => {
  const rows = root.querySelectorAll('[data-reception-row]');
  rows.forEach((row) => {
    const requested = Number(row.dataset.requested || 0);
    const input = row.querySelector('input[name*="[received_quantity]"]');
    const pendingCell = row.querySelector('[data-pending-cell]');

    function syncPending() {
      if (!pendingCell || !input) {
        return;
      }
      const received = parseFloat(input.value);
      const pending = requested - (Number.isNaN(received) ? 0 : received);
      pendingCell.textContent = formatNumber(pending);
    }

    if (input) {
      input.addEventListener('input', syncPending);
      input.addEventListener('change', syncPending);
      syncPending();
    }
  });
});

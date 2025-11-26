(function () {
  function onReady(callback) {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', callback);
    } else {
      callback();
    }
  }

  function parseNumber(value) {
    if (typeof value === 'number') {
      return value;
    }
    if (typeof value === 'string') {
      var normalized = value.replace(/,/g, '.');
      var parsed = parseFloat(normalized);
      return isNaN(parsed) ? 0 : parsed;
    }
    return 0;
  }

  function formatCurrency(value) {
    var amount = typeof value === 'number' ? value : parseFloat(value);
    if (isNaN(amount)) {
      amount = 0;
    }
    var sign = amount < 0 ? '-' : '';
    var absolute = Math.abs(Math.round(amount));
    var chunks = [];
    var chunk;
    while (absolute >= 1000) {
      chunk = String(absolute % 1000).padStart(3, '0');
      chunks.unshift(chunk);
      absolute = Math.floor(absolute / 1000);
    }
    chunks.unshift(String(absolute));
    return '$ ' + sign + chunks.join('.');
  }

  function formatCartons(value) {
    var amount = typeof value === 'number' ? value : parseFloat(value);
    if (isNaN(amount)) {
      return '—';
    }
    try {
      return new Intl.NumberFormat('es-CO', {
        minimumFractionDigits: 0,
        maximumFractionDigits: 1,
      }).format(amount);
    } catch (error) {
      return amount.toFixed(1);
    }
  }

  function initTabs(root) {
    if (!root) {
      return;
    }
    var triggers = Array.prototype.slice.call(root.querySelectorAll('[data-sale-tab-trigger]'));
    var panels = Array.prototype.slice.call(root.querySelectorAll('[data-sale-tab-panel]'));
    if (!triggers.length || !panels.length) {
      return;
    }

    function applyActive(tabName) {
      var current = tabName || 'form';
      root.setAttribute('data-sale-active-tab', current);
      panels.forEach(function (panel) {
        var panelName = panel.getAttribute('data-sale-tab-panel');
        var isActivePanel = panelName === current;
        panel.classList.toggle('hidden', !isActivePanel);
      });
      triggers.forEach(function (trigger) {
        var triggerName = trigger.getAttribute('data-sale-tab-trigger');
        var isActiveTrigger = triggerName === current;
        trigger.classList.toggle('border-emerald-200', isActiveTrigger);
        trigger.classList.toggle('bg-emerald-50', isActiveTrigger);
        trigger.classList.toggle('text-emerald-700', isActiveTrigger);
        trigger.classList.toggle('border-transparent', !isActiveTrigger);
        trigger.classList.toggle('text-slate-500', !isActiveTrigger);
        trigger.setAttribute('aria-selected', isActiveTrigger ? 'true' : 'false');
        trigger.setAttribute('data-sale-selected', isActiveTrigger ? 'true' : 'false');
      });
    }

    var initialTab = root.getAttribute('data-sale-active-tab') || 'form';
    applyActive(initialTab);

    triggers.forEach(function (trigger) {
      trigger.addEventListener('click', function (event) {
        event.preventDefault();
        var targetTab = trigger.getAttribute('data-sale-tab-trigger') || 'form';
        applyActive(targetTab);
      });
    });
  }

  onReady(function () {
    var tabsRoot = document.querySelector('[data-sale-tabs]');
    if (tabsRoot) {
      initTabs(tabsRoot);
    }
    var form = document.querySelector('[data-sale-form-panel]');
    if (!form) {
      return;
    }

    var productRows = Array.prototype.slice.call(form.querySelectorAll('[data-sale-product-row]'));
    var discountField = form.querySelector('[name="discount_amount"]');
    var summaryTargets = {
      subtotal: document.querySelectorAll('[data-sale-summary="subtotal"]'),
      discount: document.querySelectorAll('[data-sale-summary="discount"]'),
      total: document.querySelectorAll('[data-sale-summary="total"]'),
      withholding: document.querySelectorAll('[data-sale-summary="withholding"]'),
    };

    function updateSummaryNodes(nodes, text) {
      if (!nodes || nodes.length === 0) {
        return;
      }
      nodes.forEach(function (node) {
        node.textContent = text;
      });
    }

    function getInputValue(fieldName) {
      if (!fieldName) {
        return 0;
      }
      var input = form.querySelector('[name="' + fieldName + '"]');
      if (!input) {
        return 0;
      }
      return parseNumber(input.value);
    }

    function updateRow(row) {
      var quantityName = row.getAttribute('data-sale-quantity-name');
      var priceName = row.getAttribute('data-sale-price-name');
      var availableAttr = row.getAttribute('data-sale-available');
      var quantity = getInputValue(quantityName);
      var price = getInputValue(priceName);
      var subtotal = quantity * price;
      row.dataset.subtotal = subtotal > 0 ? String(subtotal) : '0';

      var subtotalTarget = row.querySelector('[data-sale-subtotal]');
      if (subtotalTarget) {
        subtotalTarget.textContent = subtotal > 0 ? formatCurrency(subtotal) : '—';
      }

      var available = availableAttr ? parseNumber(availableAttr) : NaN;
      var availableLabel = row.querySelector('[data-sale-inventory-available]');
      var remainingLabel = row.querySelector('[data-sale-inventory-remaining]');
      if (availableLabel) {
        availableLabel.textContent = isNaN(available)
          ? '—'
          : formatCartons(available) + ' cart disponibles';
      }
      if (remainingLabel) {
        if (isNaN(available)) {
          remainingLabel.textContent = 'Saldo tras pedido: —';
        } else {
          var remaining = available - quantity;
          if (remaining < 0) {
            remaining = 0;
          }
          remainingLabel.textContent = 'Saldo tras pedido: ' + formatCartons(remaining);
        }
      }
    }

    function computeSubtotal() {
      return productRows.reduce(function (total, row) {
        var subtotalValue = parseNumber(row.dataset.subtotal || '0');
        return total + subtotalValue;
      }, 0);
    }

    function refreshSummary() {
      productRows.forEach(updateRow);
      var subtotal = computeSubtotal();
      var discount = discountField ? parseNumber(discountField.value) : 0;
      if (discount < 0) {
        discount = 0;
      }
      if (discount > subtotal) {
        discount = subtotal;
      }
      var total = subtotal - discount;
      if (total < 0) {
        total = 0;
      }
      var withholding = total * 0.01;
      updateSummaryNodes(summaryTargets.subtotal, formatCurrency(subtotal));
      updateSummaryNodes(summaryTargets.discount, formatCurrency(discount));
      updateSummaryNodes(summaryTargets.total, formatCurrency(total));
      updateSummaryNodes(summaryTargets.withholding, formatCurrency(withholding));
    }

    function attachListeners() {
      productRows.forEach(function (row) {
        var quantityName = row.getAttribute('data-sale-quantity-name');
        var priceName = row.getAttribute('data-sale-price-name');
        var quantityInput = quantityName ? form.querySelector('[name="' + quantityName + '"]') : null;
        var priceInput = priceName ? form.querySelector('[name="' + priceName + '"]') : null;
        if (quantityInput) {
          quantityInput.addEventListener('input', refreshSummary);
          quantityInput.addEventListener('change', refreshSummary);
        }
        if (priceInput) {
          priceInput.addEventListener('input', refreshSummary);
          priceInput.addEventListener('change', refreshSummary);
        }
      });
      if (discountField) {
        discountField.addEventListener('input', refreshSummary);
        discountField.addEventListener('change', refreshSummary);
      }
    }

    attachListeners();
    refreshSummary();
  });
})();

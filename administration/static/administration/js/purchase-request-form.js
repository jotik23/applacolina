const purchaseForms = document.querySelectorAll('[data-purchase-request-form]');

purchaseForms.forEach((root) => {
  const state = {
    readOnly: root.getAttribute('data-read-only') === 'true',
    unitLabel: root.getAttribute('data-unit-label') || 'Unidad',
  };

  const categorySelect = root.querySelector('[data-category-select]');
  const scopeContainer =
    root.querySelector('[data-scope-container]') || root.querySelector('[data-scope-fields]');
  const scopeFarmSelect = root.querySelector('[data-scope-farm]');
  const scopeBatchSelect = root.querySelector('[data-scope-batch]');
  const scopeHouseInput = root.querySelector('input[name="scope_chicken_house_id"]');
  const supplierSelect = root.querySelector('[data-supplier-select]');
  const supplierCreateUrl = root.getAttribute('data-supplier-create-url');
  const supportTypeSelect = root.querySelector('[data-support-type-select]');
  const itemsRoot = root.querySelector('[data-purchase-items-root]');
  const addItemButton = root.querySelector('[data-add-item]');
  const totalDisplay = root.querySelector('[data-purchase-total]');
  const template = document.getElementById('purchase-item-row-template');
  const csrfInput =
    root.querySelector('input[name="csrfmiddlewaretoken"]') ||
    document.querySelector('input[name="csrfmiddlewaretoken"]');

  function getCsrfToken() {
    if (csrfInput && csrfInput.value) {
      return csrfInput.value;
    }
    const cookieMatch = document.cookie.match(/csrftoken=([^;]+)/);
    if (cookieMatch) {
      return cookieMatch[1];
    }
    return '';
  }

  function toggleField(field, visible) {
    if (!field) {
      return;
    }
    if (visible) {
      field.removeAttribute('hidden');
    } else {
      field.setAttribute('hidden', 'true');
    }
  }

  function isLocationScope(scope) {
    return scope === 'farm' || scope === 'lot';
  }

  function applyScope(scope) {
    if (!scopeContainer) {
      return;
    }
    const activeScope = scope || '';
    scopeContainer.dataset.activeScope = activeScope;
    const locationScope = isLocationScope(scope);
    const farmField = scopeContainer.querySelector('[data-scope-field="farm"]');
    const batchField = scopeContainer.querySelector('[data-scope-field="batch"]');
    toggleField(farmField, scope === 'farm');
    toggleField(batchField, scope === 'lot');
    updateScopeMeta(scope);
    if (!locationScope && scopeFarmSelect) {
      scopeFarmSelect.value = '';
    } else if (scope !== 'farm' && scopeFarmSelect) {
      scopeFarmSelect.value = '';
    }
    if (scope !== 'lot' && scopeBatchSelect) {
      scopeBatchSelect.value = '';
    }
  }

  function updateScopeFromSelection(options = {}) {
    if (!categorySelect) {
      return;
    }
    const selectedOption = categorySelect.options[categorySelect.selectedIndex];
    const scopeValue = selectedOption ? selectedOption.dataset.scope : null;
    applyScope(scopeValue);
    if (scopeValue !== 'house' && scopeHouseInput) {
      scopeHouseInput.value = '';
    }
    updateUnitLabel(selectedOption ? selectedOption.dataset.unit : null);
    applySupportTypeFromCategory(selectedOption, { force: Boolean(options.forceSupportType) });
  }

  function applySupportTypeFromCategory(option, { force = false } = {}) {
    if (!supportTypeSelect || !option) {
      return;
    }
    const supportType = option.dataset.supportTypeId || '';
    if (!supportType) {
      return;
    }
    if (!force && supportTypeSelect.value) {
      return;
    }
    supportTypeSelect.value = supportType;
  }

  function formatNumber(value) {
    const number = Number.isNaN(value) ? 0 : value;
    return number.toLocaleString('es-CO', {
      minimumFractionDigits: number % 1 === 0 ? 0 : 2,
      maximumFractionDigits: 2,
    });
  }

  function getNumericValue(input) {
    if (!input) {
      return 0;
    }
    const raw = parseFloat(input.value);
    return Number.isNaN(raw) ? 0 : raw;
  }

  function syncRowSubtotal(row) {
    if (!row) {
      return 0;
    }
    const quantityInput = row.querySelector('input[data-field="quantity"]');
    const amountInput = row.querySelector('input[data-field="estimated_amount"]');
    const subtotalCell = row.querySelector('[data-item-subtotal]');
    const subtotal = getNumericValue(quantityInput) * getNumericValue(amountInput);
    if (subtotalCell) {
      subtotalCell.textContent = formatNumber(subtotal);
    }
    return subtotal;
  }

  function reindexRows() {
    if (!itemsRoot) {
      return;
    }
    const rows = Array.from(itemsRoot.querySelectorAll('[data-item-row]'));
    rows.forEach((row, index) => {
      row.dataset.index = String(index);
      Array.from(row.querySelectorAll('[data-field]')).forEach((input) => {
        const field = input.getAttribute('data-field');
        if (!field) {
          return;
        }
        input.setAttribute('name', `items[${index}][${field}]`);
      });
    });
  }

  function syncTotals() {
    if (!itemsRoot || !totalDisplay) {
      return;
    }
    const rows = Array.from(itemsRoot.querySelectorAll('[data-item-row]'));
    const total = rows.reduce((acc, row) => acc + syncRowSubtotal(row), 0);
    totalDisplay.textContent = formatNumber(total);
  }

  function updateUnitLabel(label) {
    state.unitLabel = (label && label.trim()) || root.getAttribute('data-unit-label') || 'Unidad';
    Array.from(root.querySelectorAll('[data-unit-chip]')).forEach((chip) => {
      chip.textContent = state.unitLabel;
    });
  }

  function updateScopeMeta(scope) {
    const locationScope = isLocationScope(scope);
    if (scopeContainer) {
      toggleField(scopeContainer, locationScope);
    }
  }

  function ensureAtLeastOneRow() {
    if (!itemsRoot) {
      return;
    }
    const rows = itemsRoot.querySelectorAll('[data-item-row]');
    if (!rows.length) {
      addRow();
    }
  }

  function attachRowEvents(row) {
    if (!row) {
      return;
    }
    const amountInput = row.querySelector('input[data-field="estimated_amount"]');
    const quantityInput = row.querySelector('input[data-field="quantity"]');
    const removeButton = row.querySelector('[data-remove-item]');
    [amountInput, quantityInput].forEach((input) => {
      if (input) {
        input.addEventListener('input', syncTotals);
      }
    });
    if (removeButton && !state.readOnly) {
      removeButton.addEventListener('click', () => {
        row.remove();
        ensureAtLeastOneRow();
        reindexRows();
        syncTotals();
      });
    } else if (removeButton && state.readOnly) {
      removeButton.disabled = true;
    }
  }

  function hydrateRow(row, initialValues = {}) {
    Array.from(row.querySelectorAll('[data-field]')).forEach((input) => {
      const field = input.getAttribute('data-field');
      if (!field) {
        return;
      }
      input.value = Object.prototype.hasOwnProperty.call(initialValues, field) ? initialValues[field] : '';
    });
    const chips = row.querySelectorAll('[data-unit-chip]');
    chips.forEach((chip) => {
      chip.textContent = state.unitLabel;
    });
    syncRowSubtotal(row);
  }

  function upsertSupplierOption(option) {
    if (!supplierSelect || !option || !option.id) {
      return;
    }
    let existing = supplierSelect.querySelector(`option[value="${option.id}"]`);
    if (!existing) {
      existing = document.createElement('option');
      existing.value = option.id;
      supplierSelect.appendChild(existing);
    }
    existing.textContent = option.display || option.name || `Proveedor ${option.id}`;
    supplierSelect.value = option.id;
    supplierSelect.dispatchEvent(new Event('change', { bubbles: true }));
  }

  function initQuickSupplierForm() {
    if (state.readOnly || !supplierSelect) {
      return;
    }
    const panel = root.querySelector('[data-new-supplier-panel]');
    const formContainer = root.querySelector('[data-new-supplier-form]');
    if (!panel || !formContainer) {
      return;
    }
    const toggleButton = root.querySelector('[data-new-supplier-toggle]');
    const cancelButton = panel.querySelector('[data-new-supplier-cancel]');
    const resetButton = panel.querySelector('[data-new-supplier-reset]');
    const submitButton = panel.querySelector('[data-new-supplier-submit]');
    const feedbackBox = panel.querySelector('[data-new-supplier-feedback]');
    const inputs = Array.from(formContainer.querySelectorAll('[data-new-supplier-field]'));
    const quickCreateEnabled = Boolean(supplierCreateUrl);
    const nameField = inputs.find((input) => input.name === 'name');
    const taxField = inputs.find((input) => input.name === 'tax_id');
    const holderIdField = inputs.find((input) => input.name === 'account_holder_id');
    const holderNameField = inputs.find((input) => input.name === 'account_holder_name');

    function setFeedback(message, variant = 'info') {
      if (!feedbackBox) {
        return;
      }
      if (!message) {
        feedbackBox.hidden = true;
        feedbackBox.textContent = '';
        return;
      }
      const variants = {
        info: 'border-blue-200 bg-blue-50 text-blue-800',
        success: 'border-emerald-200 bg-emerald-50 text-emerald-800',
        error: 'border-red-200 bg-red-50 text-red-700',
      };
      feedbackBox.className = `rounded-xl border px-3 py-2 text-[11px] font-semibold ${variants[variant] || variants.info}`;
      feedbackBox.textContent = message;
      feedbackBox.hidden = false;
    }

    function clearInputs() {
      inputs.forEach((input) => {
        if (input) {
          input.value = '';
        }
      });
    }

    function maybeSyncHolderDefaults() {
      if (holderIdField && !holderIdField.value && taxField) {
        holderIdField.value = taxField.value;
      }
      if (holderNameField && !holderNameField.value && nameField) {
        holderNameField.value = nameField.value;
      }
    }

    function handlePanelOpen() {
      if (!quickCreateEnabled) {
        setFeedback('Esta instancia no permite crear terceros desde aquí.', 'error');
      } else {
        setFeedback('');
      }
      maybeSyncHolderDefaults();
      if (inputs[0]) {
        inputs[0].focus();
      }
    }

    function handlePanelClose() {
      setFeedback('');
    }

    if (!quickCreateEnabled && submitButton) {
      submitButton.disabled = true;
    }

    const supportsDetailsToggle = typeof panel.open === 'boolean';

    if (supportsDetailsToggle) {
      panel.addEventListener('toggle', () => {
        if (panel.open) {
          handlePanelOpen();
        } else {
          handlePanelClose();
        }
      });
    } else if (toggleButton) {
      toggleButton.addEventListener('click', (event) => {
        event.preventDefault();
        const willOpen = panel.hasAttribute('hidden');
        panel.hidden = !willOpen;
        if (willOpen) {
          handlePanelOpen();
        } else {
          handlePanelClose();
        }
      });
    }

    if (cancelButton) {
      cancelButton.addEventListener('click', () => {
        if (supportsDetailsToggle) {
          panel.open = false;
        } else {
          panel.hidden = true;
        }
        handlePanelClose();
      });
    }

    async function submitSupplier(event) {
      event.preventDefault();
      if (!submitButton) {
        return;
      }
      if (!supplierCreateUrl) {
        setFeedback('Esta instancia no permite crear terceros desde aquí.', 'error');
        return;
      }
      const formData = new FormData();
      inputs.forEach((input) => {
        if (input && input.name) {
          formData.append(input.name, input.value.trim());
        }
      });
      submitButton.disabled = true;
      setFeedback('Guardando tercero...', 'info');
      try {
        const response = await fetch(supplierCreateUrl, {
          method: 'POST',
          headers: {
            'X-CSRFToken': getCsrfToken(),
            'X-Requested-With': 'XMLHttpRequest',
          },
          body: formData,
        });
        const payload = await response.json();
        if (!response.ok) {
          const errors = payload.errors
            ? Object.values(payload.errors)
                .flat()
                .join(' ')
            : 'No se pudo registrar el tercero.';
          setFeedback(errors, 'error');
          return;
        }
        upsertSupplierOption(payload.supplier);
        setFeedback('Tercero registrado y seleccionado automáticamente.', 'success');
        setTimeout(() => {
          if (supportsDetailsToggle) {
            panel.open = false;
          } else {
            panel.hidden = true;
          }
          clearInputs();
          setFeedback('');
        }, 1200);
      } catch (error) {
        // eslint-disable-next-line no-console
        console.error('Error guardando proveedor', error);
        setFeedback('Ocurrió un error inesperado. Intenta de nuevo.', 'error');
      } finally {
        submitButton.disabled = false;
      }
    }

    function registerManualOverride(field) {
      if (!field) {
        return;
      }
      field.addEventListener('input', () => {
        field.dataset.manualOverride = field.value ? 'true' : 'false';
      });
    }

    registerManualOverride(holderIdField);
    registerManualOverride(holderNameField);

    if (nameField) {
      nameField.addEventListener('input', () => {
        if (holderNameField && holderNameField.dataset.manualOverride !== 'true') {
          holderNameField.value = nameField.value;
        }
      });
    }

    if (taxField) {
      taxField.addEventListener('input', () => {
        if (holderIdField && holderIdField.dataset.manualOverride !== 'true') {
          holderIdField.value = taxField.value;
        }
      });
    }

    if (resetButton) {
      resetButton.addEventListener('click', (event) => {
        event.preventDefault();
        clearInputs();
        setFeedback('');
        if (holderIdField) {
          holderIdField.dataset.manualOverride = 'false';
        }
        if (holderNameField) {
          holderNameField.dataset.manualOverride = 'false';
        }
      });
    }

    if (submitButton) {
      submitButton.addEventListener('click', submitSupplier);
    }
  }

  function addRow(initialValues = {}) {
    if (!itemsRoot || !template) {
      return;
    }
    const fragment = template.content.cloneNode(true);
    const row = fragment.querySelector('[data-item-row]');
    if (!row) {
      return;
    }
    hydrateRow(row, initialValues);
    itemsRoot.appendChild(row);
    attachRowEvents(row);
    reindexRows();
    syncTotals();
  }

  function setupExistingRows() {
    if (!itemsRoot) {
      return;
    }
    const rows = Array.from(itemsRoot.querySelectorAll('[data-item-row]'));
    rows.forEach((row) => {
      const values = {};
      Array.from(row.querySelectorAll('[data-field]')).forEach((input) => {
        values[input.getAttribute('data-field')] = input.value;
      });
      hydrateRow(row, values);
      attachRowEvents(row);
    });
    ensureAtLeastOneRow();
    reindexRows();
    syncTotals();
  }

  function setupScopeSelectors() {
    if (scopeFarmSelect) {
      scopeFarmSelect.addEventListener('change', () => {
        syncTotals();
      });
    }
  }

  function setupAddButton() {
    if (!addItemButton) {
      return;
    }
    if (state.readOnly) {
      addItemButton.disabled = true;
      return;
    }
    addItemButton.addEventListener('click', () => addRow());
  }

  function init() {
    updateScopeMeta(scopeContainer ? scopeContainer.dataset.activeScope : null);
    updateScopeFromSelection();
    if (categorySelect) {
      categorySelect.addEventListener('change', () => updateScopeFromSelection({ forceSupportType: true }));
    }
    setupExistingRows();
    setupScopeSelectors();
    setupAddButton();
    initQuickSupplierForm();
  }

  init();
});

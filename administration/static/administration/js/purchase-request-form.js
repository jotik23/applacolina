const root = document.querySelector('[data-purchase-request-form]');

if (root) {
  const state = {
    readOnly: root.getAttribute('data-read-only') === 'true',
    unitLabel: root.getAttribute('data-unit-label') || 'Unidad',
  };

  const categorySelect = root.querySelector('[data-category-select]');
  const scopeFields = root.querySelector('[data-scope-fields]');
  const scopeFarmSelect = root.querySelector('[data-scope-farm]');
  const scopeHouseSelect = root.querySelector('[data-scope-house]');
  const scopeBatchInput = root.querySelector('[data-scope-batch]');
  const supportTypeSelect = root.querySelector('[data-support-type-select]');
  const itemsRoot = root.querySelector('[data-purchase-items-root]');
  const addItemButton = root.querySelector('[data-add-item]');
  const totalDisplay = root.querySelector('[data-purchase-total]');
  const template = document.getElementById('purchase-item-row-template');
  const scopePill = root.querySelector('[data-scope-pill]');
  const scopeEmptyMsg = root.querySelector('[data-scope-empty]');
  const scopeHelperMsg = root.querySelector('[data-scope-helper]');

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

  function applyScope(scope) {
    if (!scopeFields) {
      return;
    }
    scopeFields.dataset.activeScope = scope || '';
    const farmField = scopeFields.querySelector('[data-scope-field="farm"]');
    const houseField = scopeFields.querySelector('[data-scope-field="house"]');
    const batchField = scopeFields.querySelector('[data-scope-field="batch"]');
    toggleField(farmField, scope === 'farm');
    toggleField(houseField, scope === 'house');
    toggleField(batchField, scope === 'lot');
    updateScopeMeta(scope, scopeFields.dataset.scopeLabel || '');
    if (scope !== 'farm' && scopeFarmSelect) {
      if (scope === 'house') {
        syncFarmFromHouse();
      } else {
        scopeFarmSelect.value = '';
      }
    }
  }

  function updateScopeFromSelection(options = {}) {
    if (!categorySelect) {
      return;
    }
    const selectedOption = categorySelect.options[categorySelect.selectedIndex];
    const scopeValue = selectedOption ? selectedOption.dataset.scope : null;
    if (scopeFields) {
      scopeFields.dataset.scopeLabel = selectedOption ? selectedOption.dataset.scopeLabel || '' : '';
    }
    applyScope(scopeValue);
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

  function filterHouseOptions(selectElement, farmId) {
    if (!selectElement) {
      return;
    }
    const desiredFarm = farmId ? String(farmId) : null;
    Array.from(selectElement.options).forEach((option) => {
      const optionFarm = option.dataset.farm || null;
      const shouldHide = Boolean(desiredFarm && optionFarm && optionFarm !== desiredFarm);
      option.hidden = shouldHide;
      if (shouldHide && option.selected) {
        option.selected = false;
      }
    });
  }

  function updateHouseFilter() {
    if (!scopeHouseSelect) {
      return;
    }
    filterHouseOptions(scopeHouseSelect, scopeFarmSelect ? scopeFarmSelect.value : null);
    if (scopeFields && scopeFields.dataset.activeScope === 'house') {
      syncFarmFromHouse();
    }
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

  function formatNumber(value) {
    const number = Number.isNaN(value) ? 0 : value;
    return number.toLocaleString('es-CO', {
      minimumFractionDigits: 0,
      maximumFractionDigits: 2,
    });
  }

  function syncTotals() {
    if (!itemsRoot || !totalDisplay) {
      return;
    }
    const amounts = Array.from(itemsRoot.querySelectorAll('input[data-field="estimated_amount"]'));
    const total = amounts.reduce((acc, input) => {
      const raw = parseFloat(input.value);
      return acc + (Number.isNaN(raw) ? 0 : raw);
    }, 0);
    totalDisplay.textContent = formatNumber(total);
  }

  function updateUnitLabel(label) {
    state.unitLabel = (label && label.trim()) || root.getAttribute('data-unit-label') || 'Unidad';
    Array.from(root.querySelectorAll('[data-unit-chip]')).forEach((chip) => {
      chip.textContent = state.unitLabel;
    });
  }

  function updateScopeMeta(scope, label) {
    if (scopePill) {
      scopePill.textContent = scope ? label || 'Ubicación seleccionada' : 'Selecciona una categoría';
    }
    if (scopeEmptyMsg) {
      scopeEmptyMsg.hidden = Boolean(scope);
    }
    if (scopeHelperMsg) {
      const needsHelper = scope === 'farm' || scope === 'house' || scope === 'lot';
      scopeHelperMsg.hidden = !needsHelper;
    }
  }

  function syncFarmFromHouse() {
    if (!scopeHouseSelect || !scopeFarmSelect) {
      return;
    }
    const option = scopeHouseSelect.options[scopeHouseSelect.selectedIndex];
    const farmId = option ? option.dataset.farm || '' : '';
    scopeFarmSelect.value = farmId;
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
    const removeButton = row.querySelector('[data-remove-item]');
    if (amountInput) {
      amountInput.addEventListener('input', syncTotals);
    }
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
      scopeFarmSelect.addEventListener('change', updateHouseFilter);
      updateHouseFilter();
    }
    if (scopeHouseSelect) {
      scopeHouseSelect.addEventListener('change', () => {
        syncFarmFromHouse();
        syncTotals();
      });
    }
  }

  function setupBatchInput() {
    if (!scopeBatchInput) {
      return;
    }
    scopeBatchInput.addEventListener('input', () => {
      scopeBatchInput.value = scopeBatchInput.value.trimStart();
    });
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
    updateScopeMeta(scopeFields ? scopeFields.dataset.activeScope : null, scopeFields ? scopeFields.dataset.scopeLabel : '');
    updateScopeFromSelection();
    if (categorySelect) {
      categorySelect.addEventListener('change', () => updateScopeFromSelection({ forceSupportType: true }));
    }
    setupExistingRows();
    setupScopeSelectors();
    setupBatchInput();
    setupAddButton();
  }

  init();
}

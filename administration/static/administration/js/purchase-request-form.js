let activeSelectPicker = null;
let selectPickerGlobalsBound = false;
const purchaseProductCatalog = (() => {
  const script = document.getElementById('purchase-products-data');
  if (!script) {
    return { list: [], byId: new Map(), byName: new Map() };
  }
  try {
    const list = JSON.parse(script.textContent || '[]');
    const byId = new Map();
    const byName = new Map();
    list.forEach((product) => {
      const id = String(product.id);
      const name = typeof product.name === 'string' ? product.name.trim() : '';
      if (id) {
        byId.set(id, product);
      }
      if (name) {
        byName.set(name.toLowerCase(), product);
      }
    });
    return { list, byId, byName };
  } catch (error) {
    console.warn('No fue posible cargar el catálogo de productos.', error);
    return { list: [], byId: new Map(), byName: new Map() };
  }
})();

const PRODUCT_SUGGESTION_OFFSET_PX = 4;
const productSuggestionPanel = (() => {
  const panel = document.createElement('div');
  panel.className =
    'product-suggestions hidden fixed z-50 max-h-64 overflow-y-auto rounded-xl border border-slate-200 bg-white shadow-2xl';
  return panel;
})();

let productSuggestionContext = null;

function mountProductSuggestionPanel() {
  if (!document.body.contains(productSuggestionPanel)) {
    document.body.appendChild(productSuggestionPanel);
  }
}

function positionProductSuggestionPanel(anchor) {
  if (!anchor) {
    return;
  }
  const bounds = anchor.getBoundingClientRect();
  productSuggestionPanel.style.width = `${bounds.width}px`;
  productSuggestionPanel.style.left = `${bounds.left}px`;
  productSuggestionPanel.style.top = `${bounds.bottom + PRODUCT_SUGGESTION_OFFSET_PX}px`;
}

function hideProductSuggestions() {
  if (!productSuggestionContext) {
    return;
  }
  productSuggestionPanel.classList.add('hidden');
  productSuggestionPanel.innerHTML = '';
  productSuggestionPanel.style.left = '';
  productSuggestionPanel.style.top = '';
  productSuggestionPanel.style.width = '';
  if (productSuggestionPanel.parentNode) {
    productSuggestionPanel.parentNode.removeChild(productSuggestionPanel);
  }
  productSuggestionContext = null;
}

function showProductSuggestions(input, { forceQuery = null, allowEmpty = false } = {}) {
  if (!input) {
    return;
  }
  const querySource = forceQuery !== null ? forceQuery : input.value;
  const query = (querySource || '').trim().toLowerCase();
  let matches = purchaseProductCatalog.list;
  if (query) {
    matches = matches.filter((product) => product.name.toLowerCase().includes(query));
  }
  if (!matches.length) {
    if (!allowEmpty) {
      hideProductSuggestions();
      return;
    }
  }
  const limitedMatches = matches.slice(0, 10);
  const pickerWrapper = input.closest('[data-product-picker]');
  if (!pickerWrapper) {
    return;
  }
  mountProductSuggestionPanel();
  productSuggestionPanel.innerHTML = '';
  if (!limitedMatches.length) {
    const emptyMessage = document.createElement('p');
    emptyMessage.className = 'px-3 py-2 text-[11px] font-semibold text-slate-400';
    emptyMessage.textContent = query ? 'Sin coincidencias' : 'No hay productos registrados';
    productSuggestionPanel.appendChild(emptyMessage);
  } else {
    limitedMatches.forEach((product) => {
      const optionButton = document.createElement('button');
      optionButton.type = 'button';
      optionButton.className = 'flex w-full flex-col rounded-xl px-3 py-2 text-left text-sm font-semibold text-slate-600 hover:bg-slate-50';
      optionButton.dataset.productOption = String(product.id);
      optionButton.addEventListener('mousedown', (event) => event.preventDefault());
      optionButton.addEventListener('click', (event) => {
        event.preventDefault();
        input.dataset.productSelection = 'true';
        applyProductToInput(input, product);
        hideProductSuggestions();
        input.focus();
      });
      const labelSpan = document.createElement('span');
      labelSpan.textContent = product.name;
      optionButton.appendChild(labelSpan);
      if (product.unit) {
        const unitSpan = document.createElement('span');
        unitSpan.className = 'text-[11px] font-normal text-slate-500';
        unitSpan.textContent = product.unit;
        optionButton.appendChild(unitSpan);
      }
      productSuggestionPanel.appendChild(optionButton);
    });
  }
  positionProductSuggestionPanel(pickerWrapper);
  productSuggestionPanel.classList.remove('hidden');
  productSuggestionContext = { input };
}

function applyProductToInput(input, product) {
  if (!input) {
    return;
  }
  input.value = product ? product.name : '';
  input.dispatchEvent(new Event('input', { bubbles: true }));
  input.dispatchEvent(new Event('change', { bubbles: true }));
}

document.addEventListener('click', (event) => {
  if (!productSuggestionContext) {
    return;
  }
  const { input } = productSuggestionContext;
  if (productSuggestionPanel.contains(event.target)) {
    return;
  }
  if (input && input.contains(event.target)) {
    return;
  }
  hideProductSuggestions();
});

window.addEventListener('scroll', () => {
  if (!productSuggestionContext) {
    return;
  }
  hideProductSuggestions();
}, true);

window.addEventListener('resize', () => {
  if (!productSuggestionContext) {
    return;
  }
  hideProductSuggestions();
});

function bindSelectPickerGlobals() {
  if (selectPickerGlobalsBound) {
    return;
  }
  document.addEventListener('click', (event) => {
    if (!activeSelectPicker) {
      return;
    }
    if (activeSelectPicker.contains(event.target)) {
      return;
    }
    closeSelectPicker(activeSelectPicker);
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && activeSelectPicker) {
      closeSelectPicker(activeSelectPicker, { focusTrigger: true });
    }
  });
  selectPickerGlobalsBound = true;
}

function openSelectPicker(picker) {
  if (activeSelectPicker && activeSelectPicker !== picker) {
    closeSelectPicker(activeSelectPicker);
  }
  const panel = picker.querySelector('[data-select-panel]');
  const chevron = picker.querySelector('[data-select-chevron]');
  const searchInput = panel ? panel.querySelector('[data-select-search]') : null;
  if (panel) {
    panel.classList.remove('hidden');
  }
  picker.setAttribute('data-open', 'true');
  if (chevron) {
    chevron.style.transform = 'rotate(180deg)';
  }
  filterSelectOptions(picker, '');
  if (searchInput) {
    searchInput.value = '';
    window.requestAnimationFrame(() => searchInput.focus());
  }
  activeSelectPicker = picker;
}

function closeSelectPicker(picker, options = {}) {
  const panel = picker.querySelector('[data-select-panel]');
  const chevron = picker.querySelector('[data-select-chevron]');
  if (panel) {
    panel.classList.add('hidden');
  }
  picker.removeAttribute('data-open');
  if (chevron) {
    chevron.style.transform = '';
  }
  if (options.focusTrigger) {
    const trigger = picker.querySelector('[data-select-trigger]');
    if (trigger) {
      trigger.focus();
    }
  }
  if (activeSelectPicker === picker) {
    activeSelectPicker = null;
  }
}

function filterSelectOptions(picker, rawQuery) {
  const query = (rawQuery || '').trim().toLowerCase();
  let visibleCount = 0;
  const groups = picker.querySelectorAll('[data-select-group]');
  groups.forEach((group) => {
    let groupVisible = 0;
    const optionButtons = group.querySelectorAll('[data-select-option]');
    optionButtons.forEach((button) => {
      const searchable = button.dataset.searchText || button.dataset.label || '';
      const match = !query || searchable.includes(query);
      const container = button.closest('li') || button;
      if (container) {
        container.classList.toggle('hidden', !match);
      }
      if (match) {
        groupVisible += 1;
      }
    });
    group.classList.toggle('hidden', groupVisible === 0);
    visibleCount += groupVisible;
  });
  const emptyState = picker.querySelector('[data-select-empty]');
  if (emptyState) {
    emptyState.classList.toggle('hidden', visibleCount > 0);
  }
}

function selectPickerValue(picker, value, { sourceButton = null, silentNative = false } = {}) {
  const placeholder = picker.getAttribute('data-select-placeholder') || '';
  const nativeSelect = picker.querySelector('[data-native-select]');
  const hiddenInput = picker.querySelector('[data-select-input]');
  const labelEl = picker.querySelector('[data-select-label]');
  const descriptionEl = picker.querySelector('[data-select-description]');
  const buttons = Array.from(picker.querySelectorAll('[data-select-option]'));
  let button = sourceButton;
  if (!button && value) {
    button = buttons.find((candidate) => (candidate.dataset.value || '') === value);
  }
  const label = button ? button.dataset.label || placeholder : placeholder;
  const description = button ? button.dataset.description || '' : '';
  if (hiddenInput) {
    hiddenInput.value = value;
  }
  if (labelEl) {
    labelEl.textContent = label || placeholder;
  }
  if (descriptionEl) {
    descriptionEl.textContent = description;
    descriptionEl.classList.toggle('hidden', !description);
  }
  buttons.forEach((candidate) => {
    const isSelected = candidate === button;
    candidate.dataset.selected = isSelected ? 'true' : 'false';
    candidate.classList.toggle('bg-slate-100', isSelected);
    candidate.classList.toggle('text-slate-900', isSelected);
    candidate.classList.toggle('text-slate-600', !isSelected);
  });
  if (!silentNative && nativeSelect) {
    nativeSelect.value = value;
    nativeSelect.dispatchEvent(new Event('change', { bubbles: true }));
  }
}

function refreshSelectClearButton(picker) {
  const clearButton = picker.querySelector('[data-select-clear]');
  if (!clearButton) {
    return;
  }
  const hiddenInput = picker.querySelector('[data-select-input]');
  const hasValue = Boolean(hiddenInput && hiddenInput.value);
  clearButton.classList.toggle('hidden', !hasValue);
  const disabled = picker.getAttribute('data-disabled') === 'true';
  clearButton.disabled = disabled;
}

function setupSelectPickers(root) {
  const pickers = root.querySelectorAll('[data-select-picker]');
  if (!pickers.length) {
    return;
  }
  bindSelectPickerGlobals();
  pickers.forEach((picker) => {
    const trigger = picker.querySelector('[data-select-trigger]');
    if (!trigger) {
      return;
    }
    const clearButton = picker.querySelector('[data-select-clear]');
    const updateClearButton = () => refreshSelectClearButton(picker);
    const nativeSelect = picker.querySelector('[data-native-select]');
    const panel = picker.querySelector('[data-select-panel]');
    const searchInput = panel ? panel.querySelector('[data-select-search]') : null;
    trigger.addEventListener('click', (event) => {
      if (trigger.hasAttribute('disabled') || picker.getAttribute('data-disabled') === 'true') {
        event.preventDefault();
        return;
      }
      event.preventDefault();
      const isOpen = picker.getAttribute('data-open') === 'true';
      if (isOpen) {
        closeSelectPicker(picker);
      } else {
        openSelectPicker(picker);
      }
    });
    if (searchInput) {
      searchInput.addEventListener('input', () => {
        filterSelectOptions(picker, searchInput.value || '');
      });
    }
    Array.from(picker.querySelectorAll('[data-select-option]')).forEach((button) => {
      button.addEventListener('click', (event) => {
        event.preventDefault();
        selectPickerValue(picker, button.dataset.value || '', { sourceButton: button });
        closeSelectPicker(picker);
        updateClearButton();
      });
    });
    if (nativeSelect) {
      nativeSelect.addEventListener('change', () => {
        selectPickerValue(picker, nativeSelect.value || '', { silentNative: true });
        updateClearButton();
      });
    }
    const initialValue = nativeSelect ? nativeSelect.value : (picker.querySelector('[data-select-input]')?.value || '');
    selectPickerValue(picker, initialValue, { silentNative: true });
    updateClearButton();
    if (clearButton) {
      clearButton.addEventListener('click', (event) => {
        event.preventDefault();
        if (clearButton.disabled) {
          return;
        }
        selectPickerValue(picker, '', { silentNative: false });
        updateClearButton();
      });
    }
  });
}

function appendSelectPickerOption(picker, option) {
  if (!picker) {
    return;
  }
  const list = picker.querySelector('[data-select-list]');
  const nativeSelect = picker.querySelector('[data-native-select]');
  if (nativeSelect) {
    let existing = nativeSelect.querySelector(`option[value="${option.value}"]`);
    if (!existing) {
      existing = document.createElement('option');
      existing.value = option.value;
      nativeSelect.appendChild(existing);
    }
    existing.textContent = option.label;
  }
  if (!list) {
    return;
  }
  const groupLabel = option.groupLabel || (option.label || '').charAt(0).toUpperCase() || '#';
  let group = list.querySelector(`[data-select-group][data-group-label="${groupLabel}"]`);
  if (!group) {
    group = document.createElement('div');
    group.className = 'py-1';
    group.setAttribute('data-select-group', '');
    group.setAttribute('data-group-label', groupLabel);
    const heading = document.createElement('p');
    heading.className = 'px-3 text-[11px] font-semibold uppercase tracking-wide text-slate-500';
    heading.textContent = groupLabel;
    const listElement = document.createElement('ul');
    listElement.className = 'mt-1 space-y-1';
    group.appendChild(heading);
    group.appendChild(listElement);
    list.appendChild(group);
  }
  const listElement = group.querySelector('ul');
  const listItem = document.createElement('li');
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'select-option-button flex w-full flex-col rounded-xl px-3 py-2 text-left text-sm font-semibold text-slate-600 hover:bg-slate-50';
  button.dataset.selectOption = 'true';
  button.dataset.value = option.value;
  button.dataset.label = option.label;
  button.dataset.description = option.description || '';
  button.dataset.searchText = (option.searchText || option.label || '').toLowerCase();
  const labelSpan = document.createElement('span');
  labelSpan.textContent = option.label;
  button.appendChild(labelSpan);
  if (option.description) {
    const descriptionSpan = document.createElement('span');
    descriptionSpan.className = 'text-[11px] font-normal text-slate-500';
    descriptionSpan.textContent = option.description;
    button.appendChild(descriptionSpan);
  }
  button.addEventListener('click', (event) => {
    event.preventDefault();
    selectPickerValue(picker, button.dataset.value || '', { sourceButton: button });
    closeSelectPicker(picker);
  });
  listItem.appendChild(button);
  listElement.appendChild(listItem);
}

const purchaseForms = document.querySelectorAll('[data-purchase-request-form]');

purchaseForms.forEach((root) => {
  const state = {
    readOnly: root.getAttribute('data-read-only') === 'true',
  };

  const categorySelect = root.querySelector('[data-category-select]');
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

  function syncCategoryEffects(options = {}) {
    if (!categorySelect) {
      return;
    }
    const selectedOption = categorySelect.options[categorySelect.selectedIndex];
    if (!selectedOption) {
      return;
    }
    applySupportTypeFromCategory(selectedOption, { force: Boolean(options.forceSupportType) });
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
    initRowProductControls(row);
  }

  function hydrateRow(row, initialValues = {}) {
    Array.from(row.querySelectorAll('[data-field]')).forEach((input) => {
      const field = input.getAttribute('data-field');
      if (!field) {
        return;
      }
      input.value = Object.prototype.hasOwnProperty.call(initialValues, field) ? initialValues[field] : '';
    });
    syncRowSubtotal(row);
  }

  function resolveProductById(productId) {
    if (!productId) {
      return null;
    }
    return purchaseProductCatalog.byId.get(String(productId)) || null;
  }

  function resolveProductByName(name) {
    if (!name) {
      return null;
    }
    return purchaseProductCatalog.byName.get(name.trim().toLowerCase()) || null;
  }

  function initRowProductControls(row) {
    const descriptionInput = row.querySelector('[data-product-input]');
    const productIdInput = row.querySelector('[data-product-id-input]');
    const picker = row.querySelector('[data-product-picker]');
    if (!descriptionInput || !productIdInput) {
      return;
    }

    function applyProductSelection(product) {
      if (product) {
        productIdInput.value = String(product.id);
      } else {
        productIdInput.value = '';
      }
    }

    function handleDescriptionChange() {
      if (state.readOnly) {
        return;
      }
      const value = descriptionInput.value || '';
      const wasPickerSelection = descriptionInput.dataset.productSelection === 'true';
      if (wasPickerSelection) {
        delete descriptionInput.dataset.productSelection;
      }
      if (!value.trim()) {
        applyProductSelection(null);
        hideProductSuggestions();
        return;
      }
      const product = resolveProductByName(value);
      applyProductSelection(product || null);
      if (!wasPickerSelection) {
        showProductSuggestions(descriptionInput);
      }
    }

    descriptionInput.addEventListener('input', handleDescriptionChange);
    descriptionInput.addEventListener('change', handleDescriptionChange);
    descriptionInput.addEventListener('focus', () => {
      if (state.readOnly) {
        return;
      }
      if ((descriptionInput.value || '').trim()) {
        showProductSuggestions(descriptionInput);
      }
    });
    descriptionInput.addEventListener('blur', () => {
      window.setTimeout(() => {
        if (!productSuggestionContext || productSuggestionContext.input !== descriptionInput) {
          hideProductSuggestions();
        }
      }, 120);
    });

    if (picker) {
      const toggleButton = picker.querySelector('[data-product-toggle]');
      if (toggleButton) {
        toggleButton.addEventListener('click', (event) => {
          event.preventDefault();
          if (state.readOnly) {
            return;
          }
          descriptionInput.focus();
          showProductSuggestions(descriptionInput, { forceQuery: '', allowEmpty: true });
        });
      }
    }

    descriptionInput.addEventListener('keydown', (event) => {
      if (event.key === 'ArrowDown' && !state.readOnly) {
        event.preventDefault();
        showProductSuggestions(descriptionInput, { forceQuery: descriptionInput.value || '', allowEmpty: true });
      }
      if (event.key === 'Escape') {
        hideProductSuggestions();
      }
    });

    const initialProduct = resolveProductById(productIdInput.value || '');
    applyProductSelection(initialProduct);
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
    const optionLabel = option.display || option.name || `Proveedor ${option.id}`;
    existing.textContent = optionLabel;
    supplierSelect.value = option.id;
    supplierSelect.dispatchEvent(new Event('change', { bubbles: true }));
    const picker = supplierSelect.closest('[data-select-picker]');
    if (picker) {
      appendSelectPickerOption(picker, {
        value: String(option.id),
        label: optionLabel,
        description: '',
        searchText: (optionLabel || '').toLowerCase(),
        groupLabel: (optionLabel || '').charAt(0).toUpperCase() || '#',
      });
    }
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
    syncCategoryEffects();
    if (categorySelect) {
      categorySelect.addEventListener('change', () => syncCategoryEffects({ forceSupportType: true }));
    }
    setupExistingRows();
    setupAddButton();
    initQuickSupplierForm();
    setupSelectPickers(root);
  }

  init();
});

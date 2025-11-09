function initPurchaseInvoiceForm() {
  const root = document.querySelector('[data-purchase-invoice-form]');
  if (!root) {
    return;
  }
  const configElement = document.getElementById('purchase-invoice-config');
  const config = configElement ? JSON.parse(configElement.textContent) : null;
  if (!config) {
    return;
  }

  const selectField = root.querySelector('select[name="support_document_type"]');
  const fieldsContainer = root.querySelector('[data-support-template-fields]');
  const emptyMessage = root.querySelector('[data-support-template-empty]');
  const resetButton = root.querySelector('[data-support-template-reset]');
  const previewFrame = root.querySelector('[data-support-template-preview]');
  const previewEmptyState = root.querySelector('[data-support-template-preview-empty]');
  const refreshButton = root.querySelector('[data-support-template-refresh]');
  const printButton = root.querySelector('[data-support-template-print]');

  const rawCatalog = config.fieldCatalog || {};
  const normalizedCatalog = new Map();
  Object.entries(rawCatalog).forEach(([key, entry]) => {
    const normalizedKey = normalizeIdentifier(key);
    if (normalizedKey) {
      normalizedCatalog.set(normalizedKey, entry);
    }
    const normalizedLabel = normalizeIdentifier(entry?.label);
    if (normalizedLabel && !normalizedCatalog.has(normalizedLabel)) {
      normalizedCatalog.set(normalizedLabel, entry);
    }
  });

  const state = {
    catalog: rawCatalog,
    catalogNormalized: normalizedCatalog,
    values: { ...(config.savedValues || {}) },
    supportTypes: new Map((config.supportTypes || []).map((type) => [type.id, type])),
    currentTypeId: config.selectedSupportTypeId || null,
    currentPlaceholders: [],
    currentTemplate: '',
  };

  function formatBytes(size) {
    if (!Number.isFinite(size) || size <= 0) {
      return '';
    }
    if (size >= 1048576) {
      return `${(size / 1048576).toFixed(1)} MB`;
    }
    return `${(size / 1024).toFixed(1)} KB`;
  }

  function updateAttachmentPreview() {
    const attachmentInput = root.querySelector('[data-invoice-attachment-input]');
    const previewList = root.querySelector('[data-invoice-attachment-preview]');
    if (!attachmentInput || !previewList) {
      return;
    }
    attachmentInput.addEventListener('change', () => {
      const files = Array.from(attachmentInput.files || []);
      previewList.innerHTML = '';
      if (!files.length) {
        previewList.hidden = true;
        return;
      }
      files.forEach((file) => {
        const item = document.createElement('li');
        item.textContent = `${file.name} · ${formatBytes(file.size)}`;
        previewList.appendChild(item);
      });
      previewList.hidden = false;
    });
  }

  function normalizeIdentifier(value) {
    return (value || '')
      .toString()
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '')
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '');
  }

  function getCatalogEntry(key) {
    const direct = state.catalog[key];
    if (direct) {
      return direct;
    }
    const normalizedKey = normalizeIdentifier(key);
    if (!normalizedKey) {
      return null;
    }
    return state.catalogNormalized.get(normalizedKey) || null;
  }

  function placeholderList(template) {
    if (!template) {
      return [];
    }
    const regex = /{{\s*([^{}]+?)\s*}}/g;
    const seen = new Set();
    const keys = [];
    let match = regex.exec(template);
    while (match) {
      const token = (match[1] || '').trim();
      if (token && !seen.has(token)) {
        seen.add(token);
        keys.push(token);
      }
      match = regex.exec(template);
    }
    return keys;
  }

  function resolveValue(key) {
    if (Object.prototype.hasOwnProperty.call(state.values, key)) {
      return state.values[key];
    }
    const catalogEntry = getCatalogEntry(key);
    if (catalogEntry) {
      return catalogEntry.value || '';
    }
    return '';
  }

  function buildFieldRow(key) {
    const wrapper = document.createElement('label');
    wrapper.className = 'block space-y-1';
    const label = document.createElement('span');
    label.className = 'text-[11px] font-semibold uppercase tracking-wide text-slate-500';
    const catalogEntry = getCatalogEntry(key);
    label.textContent = catalogEntry?.label || key;
    const input = document.createElement('input');
    input.type = 'text';
    input.name = `template_fields[${key}]`;
    input.value = resolveValue(key);
    input.className = 'w-full rounded-xl border border-slate-200 px-3 py-2 text-sm text-slate-900 shadow-sm focus:border-slate-400 focus:outline-none';
    input.dataset.templateField = key;
    const hint = document.createElement('p');
    hint.className = 'text-[11px] text-slate-500';
    const suggested = catalogEntry?.value;
    hint.textContent = suggested ? `Sugerido: ${suggested}` : 'Valor libre';
    wrapper.append(label, input, hint);
    return wrapper;
  }

  function updateFields(template) {
    if (!fieldsContainer || !emptyMessage) {
      return;
    }
    fieldsContainer.innerHTML = '';
    state.currentTemplate = template || '';
    state.currentPlaceholders = placeholderList(state.currentTemplate);
    if (!state.currentPlaceholders.length) {
      fieldsContainer.appendChild(emptyMessage);
      emptyMessage.hidden = false;
    resetButton?.setAttribute('hidden', 'hidden');
      printButton.disabled = true;
      return;
    }
    emptyMessage.hidden = true;
    state.currentPlaceholders.forEach((key) => {
      fieldsContainer.appendChild(buildFieldRow(key));
    });
    if (resetButton) {
      resetButton.hidden = false;
    }
  }

  function renderTemplate(template) {
    if (!template) {
      return '';
    }
    return template.replace(/{{\s*([^{}]+?)\s*}}/g, (_, key) => resolveValue(key));
  }

  function composePreviewDocument(bodyHtml) {
    return `<!doctype html><html><head><meta charset="utf-8"><style>
      :root { color-scheme: light; }
      html, body {
        margin: 0;
        padding: 24px;
        font-family: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        background: #fff;
        color: #0f172a;
        line-height: 1.6;
      }
      h1, h2, h3, h4 { color: #0f172a; margin-top: 1.5rem; margin-bottom: 0.75rem; }
      table { width: 100%; border-collapse: collapse; margin-bottom: 1.5rem; }
      th, td { border: 1px solid #e2e8f0; padding: 8px; text-align: left; }
      ul { padding-left: 1.25rem; }
      img { max-width: 100%; }
    </style></head><body>${bodyHtml}</body></html>`;
  }

  function updatePreview() {
    if (!previewFrame) {
      return;
    }
    const template = state.currentTemplate.trim();
    if (!template) {
      if (previewFrame) {
        previewFrame.hidden = true;
      }
      if (previewEmptyState) {
        previewEmptyState.hidden = false;
      }
      if (printButton) {
        printButton.disabled = true;
      }
      return;
    }
    const renderedHtml = renderTemplate(template);
    const doc = composePreviewDocument(renderedHtml);
    previewFrame.srcdoc = doc;
    previewFrame.hidden = false;
    if (previewEmptyState) {
      previewEmptyState.hidden = true;
    }
    if (printButton) {
      printButton.disabled = false;
    }
  }

  function refreshTemplateFromSelection() {
    const typeId = selectField ? Number(selectField.value) || null : null;
    state.currentTypeId = typeId;
    const currentType = typeId ? state.supportTypes.get(typeId) : null;
    if (!currentType || currentType.kind !== 'internal' || !(currentType.template || '').trim()) {
      updateFields('');
      updatePreview();
      return;
    }
    updateFields(currentType.template || '');
    updatePreview();
  }

  function handleFieldInput(event) {
    const target = event.target;
    if (!target || !target.dataset?.templateField) {
      return;
    }
    const key = target.dataset.templateField;
    state.values[key] = target.value;
  }

  function handleResetClick() {
    state.currentPlaceholders.forEach((key) => {
      const catalogEntry = getCatalogEntry(key);
      state.values[key] = catalogEntry?.value || '';
      const input = root.querySelector(`input[name="template_fields[${key}]"]`);
      if (input) {
        input.value = state.values[key];
      }
    });
    updatePreview();
  }

  function handlePrintClick() {
    const template = state.currentTemplate.trim();
    if (!template) {
      return;
    }
    const html = composePreviewDocument(renderTemplate(template));
    const blob = new Blob([html], { type: 'text/html' });
    const url = URL.createObjectURL(blob);
    const printWindow = window.open(url, '_blank', 'noopener');
    if (!printWindow) {
      URL.revokeObjectURL(url);
      return;
    }
    const cleanUp = () => {
      URL.revokeObjectURL(url);
      printWindow.removeEventListener('load', cleanUp);
    };
    printWindow.addEventListener('load', () => {
      printWindow.focus();
      printWindow.print();
      cleanUp();
    });
  }

  updateAttachmentPreview();
  refreshTemplateFromSelection();
  fieldsContainer?.addEventListener('input', handleFieldInput);
  refreshButton?.addEventListener('click', updatePreview);
  resetButton?.addEventListener('click', handleResetClick);
  printButton?.addEventListener('click', handlePrintClick);
  selectField?.addEventListener('change', refreshTemplateFromSelection);
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initPurchaseInvoiceForm);
} else {
  initPurchaseInvoiceForm();
}

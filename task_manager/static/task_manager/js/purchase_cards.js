(() => {
  'use strict';

  const tmMiniApp = window.tmMiniApp || (window.tmMiniApp = {});

  const supportsIntl = typeof Intl !== 'undefined' && typeof Intl.NumberFormat === 'function';

  function buildNumberFormatter(options) {
    if (supportsIntl) {
      return new Intl.NumberFormat('es-CO', options);
    }
    const config = options || {};
    const maxFraction =
      typeof config.maximumFractionDigits === 'number'
        ? config.maximumFractionDigits
        : typeof config.minimumFractionDigits === 'number'
          ? config.minimumFractionDigits
          : 0;
    return {
      format(value) {
        const number = typeof value === 'number' && !Number.isNaN(value) ? value : 0;
        const sign = number < 0 ? '-' : '';
        const absolute = Math.abs(number);
        const formatted =
          maxFraction > 0 ? absolute.toFixed(maxFraction) : Math.round(absolute).toString();
        const parts = formatted.split('.');
        parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, '.');
        if (parts.length === 1) {
          return sign + parts[0];
        }
        return sign + parts[0] + ',' + parts[1];
      },
    };
  }

  const currencyFormatter = buildNumberFormatter({
    style: 'currency',
    currency: 'COP',
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  });

  const twoDecimalsFormatter = buildNumberFormatter({
    style: 'currency',
    currency: 'COP',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });

  function resolveCurrencySymbol(value) {
    if (!value) {
      return '$';
    }
    const upperValue = String(value).toUpperCase();
    return upperValue === 'COP' ? '$' : value;
  }

  function formatCurrency(amount, currencySymbol) {
    const symbol = resolveCurrencySymbol(currencySymbol);
    if (typeof amount !== 'number' || Number.isNaN(amount)) {
      return symbol === '$' ? '$ 0' : `${symbol} 0`;
    }
    if (symbol !== '$') {
      return `${symbol} ${amount.toFixed(2)}`;
    }
  return amount >= 100000
    ? currencyFormatter.format(amount)
    : twoDecimalsFormatter.format(amount);
  }

  function safeParseJSON(value) {
    if (!value) {
      return null;
    }
    try {
      return JSON.parse(value);
    } catch (error) {
      return null;
    }
  }


  class PurchaseRequestsListController {
    constructor(card, helpers) {
      this.card = card;
      this.helpers = helpers || {};
      this.listNode = card.querySelector('[data-purchase-requests-list]');
      this.totalNode = card.querySelector('[data-purchase-requests-total]');
      this.countNode = card.querySelector('[data-purchase-requests-count]');
      this.emptyStateNode = card.querySelector('[data-purchase-requests-empty]');
      this.template = card.querySelector('template[data-purchase-request-entry-template]');
      this.summaryChips = Array.prototype.slice.call(card.querySelectorAll('[data-purchase-status-chip]'));
      this.handleListClick = this.handleListClick.bind(this);
      if (this.listNode) {
        this.listNode.addEventListener('click', this.handleListClick);
      }
    }

  render(payload) {
    if (!payload || !this.listNode) {
      return;
    }
    const tmMiniApp = window.tmMiniApp || (window.tmMiniApp = {});
    tmMiniApp.purchases = tmMiniApp.purchases || {};
    tmMiniApp.purchases.overview = payload;
    if (this.totalNode) {
      this.totalNode.textContent = payload.summary ? payload.summary.total_amount : '—';
    }
    if (this.countNode) {
      this.countNode.textContent = payload.summary
        ? `${payload.summary.total_count} solicitudes activas`
        : '0 solicitudes';
    }
    if (this.summaryChips.length && payload.summary && Array.isArray(payload.summary.status_breakdown)) {
      this.summaryChips.forEach((chip) => {
        const statusId = chip.getAttribute('data-purchase-status-chip');
        const definition = payload.summary.status_breakdown.find((entry) => entry.id === statusId);
        if (definition) {
          chip.textContent = `${definition.label} · ${definition.count}`;
        }
      });
    }
    this.listNode.innerHTML = '';
    if (payload.entries && payload.entries.length) {
      payload.entries.forEach((entry) => {
        this.listNode.appendChild(this.buildEntryNode(entry));
      });
    } else if (this.emptyStateNode) {
      const clone = this.emptyStateNode.cloneNode(true);
      clone.classList.remove('hidden');
      this.listNode.appendChild(clone);
    }
  }

  buildEntryNode(entry) {
    let node = null;
    if (this.template) {
      node = document.importNode(this.template.content, true).firstElementChild;
    }
    if (!node) {
      node = document.createElement('article');
      node.className =
        'rounded-2xl border border-slate-200 bg-white/90 p-4 shadow-inner shadow-slate-50';
    }
    node.setAttribute('data-entry-id', entry.id);
    if (entry.edit_payload) {
      node.dataset.editPayload = JSON.stringify(entry.edit_payload);
    } else if (node.dataset) {
      delete node.dataset.editPayload;
    }
    node.dataset.canEdit = entry.can_edit ? 'true' : 'false';
    const codeNode = node.querySelector('[data-entry-code]');
    const nameNode = node.querySelector('[data-entry-name]');
    const areaNode = node.querySelector('[data-entry-area]');
    const supplierNode = node.querySelector('[data-entry-supplier]');
    const categoryNode = node.querySelector('[data-entry-category]');
    const amountNode = node.querySelector('[data-entry-amount]');
    const stageChip = node.querySelector('[data-entry-stage-chip]');
    const updatedNode = node.querySelector('[data-entry-updated]');
    const statusChip = node.querySelector('[data-entry-status-chip]');
    if (codeNode) codeNode.textContent = entry.code || '';
    if (nameNode) nameNode.textContent = entry.name || '';
    if (areaNode) areaNode.textContent = entry.area_label || '';
    if (supplierNode) supplierNode.textContent = entry.supplier_label || '—';
    if (categoryNode) categoryNode.textContent = entry.category_label || '—';
    if (amountNode) amountNode.textContent = entry.amount_label || '—';
    if (stageChip) {
      stageChip.textContent = entry.stage_label || entry.status_label || '';
      this.applyStageChipTone(stageChip, !!entry.stage_is_alert);
    }
    if (updatedNode) updatedNode.textContent = entry.updated_label || '';
    if (statusChip) statusChip.textContent = entry.status_label || '';
    const editButton = node.querySelector('[data-purchase-request-edit]');
    if (editButton) {
      const shouldShowEdit = Boolean(entry.can_edit);
      editButton.classList.toggle('hidden', !shouldShowEdit);
      editButton.disabled = !shouldShowEdit;
    }
    this.populateItems(node, entry);
    this.populatePaymentDetails(node, entry);
    this.populateReceptionDetails(node, entry);
    return node;
  }

  applyStageChipTone(stageChip, isAlert) {
    const alertClasses = ['border-rose-200', 'bg-rose-50', 'text-rose-700'];
    const neutralClasses = ['border-slate-200', 'bg-slate-50', 'text-slate-600'];
    stageChip.dataset.entryStageAlert = isAlert ? 'true' : 'false';
    stageChip.classList.remove(...alertClasses, ...neutralClasses);
    stageChip.classList.add(...(isAlert ? alertClasses : neutralClasses));
  }

  populateItems(node, entry) {
    const itemsContainer = node.querySelector('[data-entry-items]');
    if (!itemsContainer) {
      return;
    }
    itemsContainer.innerHTML = '';
    if (!entry.items || !entry.items.length) {
      const empty = document.createElement('p');
      empty.className = 'text-xs text-slate-500';
      empty.textContent = 'Sin ítems registrados.';
      itemsContainer.appendChild(empty);
      return;
    }
    entry.items.forEach((item) => {
      const row = document.createElement('article');
      row.className =
        'space-y-3 rounded-3xl border border-slate-100/80 bg-white px-4 py-3 text-xs text-slate-600 shadow-sm shadow-slate-100';
      const titleWrapper = document.createElement('div');
      titleWrapper.className = 'flex items-start justify-between gap-3';
      const title = document.createElement('p');
      title.className = 'text-sm font-semibold leading-tight text-slate-900';
      title.textContent = item.description || 'Ítem solicitado';
      titleWrapper.appendChild(title);
      if (item.product_label) {
        const meta = document.createElement('p');
        meta.className = 'text-[11px] font-semibold uppercase tracking-wide text-slate-500';
        meta.textContent = item.product_label;
        titleWrapper.appendChild(meta);
      }
      row.appendChild(titleWrapper);

      const stats = document.createElement('dl');
      stats.className =
        'grid grid-cols-2 gap-x-4 gap-y-3 text-[11px] font-semibold uppercase tracking-wide text-slate-500 sm:grid-cols-3';
      const statConfigs = [
        { label: 'Solicitado', value: item.requested_label || item.quantity_label || '—' },
        { label: 'Recibido', value: item.received_label || '—' },
        { label: 'Valor unitario', value: item.unit_value_label || '—' },
      ];
      statConfigs.forEach((stat) => {
        const wrapper = document.createElement('div');
        const term = document.createElement('dt');
        term.className = 'text-[11px] font-semibold uppercase tracking-wide text-slate-500';
        term.textContent = stat.label;
        const definition = document.createElement('dd');
        definition.className = 'mt-1 text-sm font-semibold text-slate-900';
        definition.textContent = stat.value;
        wrapper.appendChild(term);
        wrapper.appendChild(definition);
        stats.appendChild(wrapper);
      });
      row.appendChild(stats);

      itemsContainer.appendChild(row);
    });
  }

  populatePaymentDetails(node, entry) {
    const container = node.querySelector('[data-entry-payment-details]');
    if (!container) {
      return;
    }
    const details = entry.payment_details || {};
    const mapping = {
      payment_method: details.payment_method_label || 'Pendiente',
      payment_condition: details.payment_condition_label || 'Pendiente',
      payment_account: details.payment_account_label || 'Sin cuenta asignada',
      payment_date: details.payment_date_label || 'Pendiente',
    };
    Object.keys(mapping).forEach((key) => {
      const target = container.querySelector(`[data-entry-payment-${key}]`);
      if (target) {
        target.textContent = mapping[key];
      }
    });
  }

  populateReceptionDetails(node, entry) {
    const container = node.querySelector('[data-entry-reception-details]');
    if (!container) {
      return;
    }
    const details = entry.reception_details || {};
    const mapping = {
      delivery_condition: details.delivery_condition_label || 'Pendiente',
      eta: details.shipping_eta_label || 'Sin fecha',
      delivery_notes: details.shipping_notes || 'Sin notas registradas.',
      reception_notes: details.reception_notes || 'Sin novedades de recepción.',
    };
    Object.keys(mapping).forEach((key) => {
      const target = container.querySelector(`[data-entry-reception-${key}]`);
      if (target) {
        target.textContent = mapping[key];
      }
    });
    const alertWrapper = container.querySelector('[data-entry-reception-alert]');
    const alertTarget = container.querySelector('[data-entry-reception-status_hint]');
    if (alertWrapper && alertTarget) {
      const hint = details.status_hint || '';
      alertTarget.textContent = hint;
      if (hint) {
        alertWrapper.classList.remove('hidden');
      } else {
        alertWrapper.classList.add('hidden');
      }
    }
  }

  handleListClick(event) {
    const trigger = event.target.closest('[data-purchase-request-edit]');
    if (!trigger) {
      return;
    }
    const entryNode = trigger.closest('[data-purchase-request-entry]');
    if (!entryNode || entryNode.dataset.canEdit !== 'true') {
      return;
    }
    const payload = this.resolveEditPayload(entryNode);
    if (typeof this.helpers.onEditRequest === 'function') {
      this.helpers.onEditRequest(payload || null);
    }
  }

  resolveEditPayload(entryNode) {
    if (!entryNode) {
      return null;
    }
    const datasetPayload = entryNode.dataset.editPayload;
    if (datasetPayload) {
      try {
        return JSON.parse(datasetPayload);
      } catch (error) {
        // Ignore invalid payloads and fall back to cached data.
      }
    }
    const entryId = entryNode.getAttribute('data-entry-id');
    const purchases = (window.tmMiniApp && window.tmMiniApp.purchases) || {};
    const overview = purchases.overview || {};
    if (!entryId || !Array.isArray(overview.entries)) {
      return null;
    }
    const matched = overview.entries.find((entry) => String(entry.id) === String(entryId));
    if (matched && matched.edit_payload) {
      return matched.edit_payload;
    }
    return null;
  }
}

class PurchaseApprovalCardController {
  constructor(card, csrfToken, helpers) {
    this.card = card;
    this.csrfToken = csrfToken || null;
    this.helpers = helpers || {};
    this.listNode = card.querySelector('[data-purchase-approval-list]');
    this.template = card.querySelector('template[data-purchase-approval-entry-template]');
    this.managerOptions = [];
  }

  init(payload) {
    this.refresh(payload || null);
  }

  refresh(payload) {
    if (!this.card) {
      return;
    }
    if (payload && Array.isArray(payload.manager_options)) {
      this.managerOptions = payload.manager_options.slice();
    }
    const tmMiniApp = window.tmMiniApp || (window.tmMiniApp = {});
    tmMiniApp.purchases = tmMiniApp.purchases || {};
    tmMiniApp.purchases.approvals = payload || null;

    const entries = (payload && payload.entries) || [];
    if (!entries.length) {
      if (this.listNode) {
        this.listNode.innerHTML = '';
      }
      this.card.classList.add('hidden');
      return;
    }
    this.card.classList.remove('hidden');
    this.renderEntries(entries);
  }

  renderEntries(entries) {
    if (!this.listNode || !this.template) {
      return;
    }
    this.listNode.innerHTML = '';
    entries.forEach((entry) => {
      const fragment = document.importNode(this.template.content, true);
      const node = fragment.querySelector('[data-purchase-approval-entry]');
      if (!node) {
        return;
      }
      node.setAttribute('data-entry-id', entry.id);
      node.setAttribute('data-decision-url', entry.decision_url || '');
      this.assignText(node, '[data-entry-name]', entry.name);
      this.assignText(node, '[data-entry-code]', entry.code);
      this.assignText(node, '[data-entry-code-inline]', entry.code);
      this.assignText(node, '[data-entry-area]', entry.area_label);
      this.assignText(node, '[data-entry-role]', entry.role_label ? `Rol: ${entry.role_label}` : '');
      this.assignText(node, '[data-entry-updated]', entry.updated_label || '');
      this.assignText(node, '[data-entry-supplier]', entry.supplier_label || '—');
      this.assignText(node, '[data-entry-amount]', entry.amount_label || '—');
      this.assignText(node, '[data-entry-status]', entry.status_label || '');
      this.applyStatusTone(node.querySelector('[data-entry-status]'), entry.status_theme);
      this.populateManagerSelect(
        node.querySelector('[data-approval-manager-select]'),
        entry.assigned_manager_id
      );
      this.renderItems(node.querySelector('[data-entry-items]'), entry.items || []);
      const feedback = node.querySelector('[data-approval-feedback]');
      if (feedback) {
        feedback.classList.add('hidden');
        feedback.textContent = '';
      }
      this.attachEntryEvents(node);
      this.listNode.appendChild(node);
    });
  }

  assignText(scope, selector, value) {
    if (!scope) {
      return;
    }
    const target = scope.querySelector(selector);
    if (target) {
      target.textContent = value || '';
    }
  }

  applyStatusTone(node, theme) {
    if (!node) {
      return;
    }
    const baseClasses = [
      'inline-flex',
      'items-center',
      'gap-2',
      'rounded-full',
      'border',
      'px-3',
      'py-1',
      'text-[11px]',
      'font-semibold',
      'uppercase',
      'tracking-wide',
    ];
    const variants = {
      amber: ['border-amber-200', 'bg-amber-50', 'text-amber-700'],
      brand: ['border-brand/40', 'bg-brand/10', 'text-brand'],
      emerald: ['border-emerald-200', 'bg-emerald-50', 'text-emerald-700'],
      indigo: ['border-indigo-200', 'bg-indigo-50', 'text-indigo-700'],
      slate: ['border-slate-200', 'bg-slate-50', 'text-slate-600'],
    };
    const classes = variants[theme] || variants.slate;
    node.className = baseClasses.join(' ');
    node.classList.add(...classes);
  }

  populateManagerSelect(select, selectedId) {
    if (!select) {
      return;
    }
    select.innerHTML = '';
    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = 'Selecciona un gestor';
    select.appendChild(placeholder);
    this.managerOptions.forEach((option) => {
      const opt = document.createElement('option');
      opt.value = String(option.id);
      opt.textContent = option.label;
      if (selectedId && String(option.id) === String(selectedId)) {
        opt.selected = true;
      }
      select.appendChild(opt);
    });
  }

  renderItems(container, items) {
    if (!container) {
      return;
    }
    container.innerHTML = '';
    if (!items.length) {
      const empty = document.createElement('p');
      empty.className = 'text-xs text-slate-500';
      empty.textContent = 'Sin ítems registrados.';
      container.appendChild(empty);
      return;
    }
    items.forEach((item) => {
      container.appendChild(this.buildItemNode(item));
    });
  }

  buildItemNode(item) {
    const node = document.createElement('article');
    node.className =
      'rounded-2xl border border-slate-100 bg-white px-3 py-2 text-xs text-slate-600 shadow-sm shadow-slate-100';
    const header = document.createElement('div');
    header.className = 'flex items-start justify-between gap-2';
    const title = document.createElement('p');
    title.className = 'text-sm font-semibold text-slate-900';
    title.textContent = item.description || 'Ítem solicitado';
    header.appendChild(title);
    if (item.product_label) {
      const badge = document.createElement('p');
      badge.className = 'text-[11px] font-semibold uppercase tracking-wide text-slate-500';
      badge.textContent = item.product_label;
      header.appendChild(badge);
    }
    node.appendChild(header);
    const stats = document.createElement('dl');
    stats.className =
      'mt-2 grid gap-2 text-[11px] uppercase tracking-wide text-slate-500 sm:grid-cols-3';
    [
      { label: 'Cantidad', value: item.requested_label || item.quantity_label || '—' },
      { label: 'Valor unitario', value: item.unit_value_label || '—' },
      { label: 'Subtotal', value: item.subtotal_label || item.amount_label || '—' },
    ].forEach((stat) => {
      const group = document.createElement('div');
      const dt = document.createElement('dt');
      dt.textContent = stat.label;
      const dd = document.createElement('dd');
      dd.className = 'mt-0.5 text-base font-semibold normal-case text-slate-900';
      dd.textContent = stat.value;
      group.appendChild(dt);
      group.appendChild(dd);
      stats.appendChild(group);
    });
    node.appendChild(stats);
    return node;
  }

  attachEntryEvents(node) {
    const buttons = node.querySelectorAll('[data-approval-action]');
    buttons.forEach((button) => {
      button.addEventListener('click', () => {
        const action = button.getAttribute('data-approval-action') || 'approve';
        this.handleDecision(node, action);
      });
    });
  }

  handleDecision(node, action) {
    if (!node) {
      return;
    }
    const url = node.getAttribute('data-decision-url');
    if (!url) {
      this.showEntryFeedback(node, 'No encontramos la ruta para registrar tu decisión.', 'error');
      return;
    }
    const managerSelect = node.querySelector('[data-approval-manager-select]');
    const noteField = node.querySelector('[data-approval-note]');
    const managerId = managerSelect ? managerSelect.value : '';
    if (!managerId) {
      this.showEntryFeedback(node, 'Selecciona el gestor asignado antes de continuar.', 'error');
      if (managerSelect && typeof managerSelect.focus === 'function') {
        try {
          managerSelect.focus({ preventScroll: true });
        } catch (error) {
          managerSelect.focus();
        }
      }
      return;
    }
    const payload = {
      assigned_manager_id: managerId,
      note: noteField ? noteField.value.trim() : '',
      decision: action === 'reject' ? 'reject' : 'approve',
    };
    this.setEntryBusy(node, true);
    this.sendDecision(url, payload)
      .then((data) => {
        if (data && data.message) {
          this.showEntryFeedback(node, data.message, 'success');
        }
        if (data && typeof this.helpers.onRequestsUpdated === 'function' && data.requests) {
          this.helpers.onRequestsUpdated(data.requests);
        }
        if (data && typeof this.helpers.onManagementUpdated === 'function' && data.management) {
          this.helpers.onManagementUpdated(data.management);
        }
        if (typeof this.helpers.onApprovalsUpdated === 'function') {
          this.helpers.onApprovalsUpdated((data && data.approvals) || null);
        } else if (data && data.approvals) {
          this.refresh(data.approvals);
        } else {
          this.refresh(null);
        }
      })
      .catch((error) => {
        const message = error && error.message ? error.message : null;
        this.showEntryFeedback(
          node,
          message || 'No pudimos registrar tu decisión. Intenta nuevamente.',
          'error'
        );
      })
      .finally(() => {
        this.setEntryBusy(node, false);
      });
  }

  setEntryBusy(node, isBusy) {
    if (!node) {
      return;
    }
    const controls = node.querySelectorAll('button, select, textarea');
    controls.forEach((control) => {
      control.disabled = isBusy;
    });
  }

  showEntryFeedback(node, message, tone) {
    if (!node) {
      return;
    }
    const feedback = node.querySelector('[data-approval-feedback]');
    if (!feedback) {
      return;
    }
    if (!message) {
      feedback.classList.add('hidden');
      feedback.textContent = '';
      return;
    }
    feedback.textContent = message;
    feedback.classList.remove('hidden', 'text-emerald-600', 'text-rose-600');
    feedback.classList.add(tone === 'success' ? 'text-emerald-600' : 'text-rose-600');
  }

  sendDecision(url, payload) {
    return fetch(url, {
      method: 'POST',
      credentials: 'include',
      headers: Object.assign(
        { 'Content-Type': 'application/json' },
        this.csrfToken ? { 'X-CSRFToken': this.csrfToken } : {}
      ),
      body: JSON.stringify(payload),
    })
      .then((response) =>
        response
          .json()
          .catch(() => ({}))
          .then((data) => ({ ok: response.ok, data }))
      )
      .then((result) => {
        if (!result.ok || (result.data && result.data.error)) {
          const message =
            (result.data && result.data.error) ||
            'No pudimos registrar tu decisión. Intenta nuevamente.';
          throw new Error(message);
        }
        return result.data || {};
      });
  }
}

  class PurchaseManagementCardController {
  constructor(card, csrfToken, helpers) {
    this.card = card;
    this.csrfToken = csrfToken || null;
    this.helpers = helpers || {};
    this.form = card.querySelector('[data-management-form]');
      this.saveButtons = Array.prototype.slice.call(
        card.querySelectorAll('[data-purchase-management-action="save"]')
      );
      this.deliveryField = this.form ? this.form.querySelector('[data-management-field="delivery_condition"]') : null;
      this.paymentMethodField = this.form ? this.form.querySelector('[data-management-field="payment_method"]') : null;
      this.shippingFields = card.querySelector('[data-management-shipping-fields]');
      this.bankPanel = card.querySelector('[data-management-bank-panel]');
      this.modifyButton = card.querySelector('[data-purchase-management-action="modify"]');
      this.finalizeButton = card.querySelector('[data-purchase-management-action="finalize"]');
      this.noteInput = card.querySelector('[data-purchase-modification-note]');
      this.feedbackNode = card.querySelector('[data-purchase-management-feedback]');
      this.orderUrl = card.getAttribute('data-order-url') || '';

      this.handleModify = this.handleModify.bind(this);
      this.handleFinalize = this.handleFinalize.bind(this);
      this.handleSave = this.handleSave.bind(this);
      this.handleDeliveryChange = this.handleDeliveryChange.bind(this);
      this.handlePaymentMethodChange = this.handlePaymentMethodChange.bind(this);
    }

    init() {
      if (this.saveButtons.length) {
        this.saveButtons.forEach((button) => {
          button.addEventListener('click', () => {
            const intent = button.getAttribute('data-management-intent') || 'save_order';
            this.handleSave(intent, button);
          });
        });
      }
      if (this.deliveryField) {
        this.deliveryField.addEventListener('change', this.handleDeliveryChange);
      }
      if (this.paymentMethodField) {
        this.paymentMethodField.addEventListener('change', this.handlePaymentMethodChange);
      }
      if (this.modifyButton) {
        this.modifyButton.addEventListener('click', this.handleModify);
      }
      if (this.finalizeButton) {
        this.finalizeButton.addEventListener('click', this.handleFinalize);
      }
      this.toggleShippingFields();
      this.toggleBankPanel();
    }

    refresh(payload) {
      const tmMiniApp = window.tmMiniApp || (window.tmMiniApp = {});
      tmMiniApp.purchases = tmMiniApp.purchases || {};
      tmMiniApp.purchases.management = payload;
      const detailView = this.card.querySelector('[data-management-view="detail"]');
      const emptyView = this.card.querySelector('[data-management-view="empty"]');
      const shouldHide = !payload || !payload.purchase || !payload.has_purchase;
      this.card.classList.toggle('hidden', shouldHide);
      if (shouldHide) {
        if (detailView) {
          detailView.classList.add('hidden');
        }
        if (emptyView) {
          emptyView.classList.remove('hidden');
          const emptyMessage = emptyView.querySelector('[data-management-empty-message]');
          if (emptyMessage && payload && payload.message) {
            emptyMessage.textContent = payload.message;
          }
        }
        this.card.setAttribute('data-allows-finalize', 'false');
        this.card.setAttribute('data-allows-modification', 'false');
        return;
      }
      if (detailView) {
        detailView.classList.remove('hidden');
      }
      if (emptyView) {
        emptyView.classList.add('hidden');
      }
      const purchase = payload.purchase;
      this.card.setAttribute('data-request-modify-url', payload.request_modification_url || '');
      this.card.setAttribute('data-finalize-url', payload.finalize_url || '');
      this.card.setAttribute('data-order-url', payload.order_url || '');
      this.orderUrl = payload.order_url || '';
      this.card.setAttribute('data-allows-finalize', payload.allow_finalize ? 'true' : 'false');
      this.card.setAttribute('data-allows-modification', payload.allow_modification ? 'true' : 'false');
      const mapping = {
        '[data-management-name]': purchase.name,
        '[data-management-location]': purchase.area_label,
        '[data-management-supplier]': purchase.supplier_label,
        '[data-management-amount]': purchase.amount_label,
        '[data-management-updated]': purchase.updated_label,
        '[data-management-code]': purchase.code,
      };
      Object.keys(mapping).forEach((selector) => {
        const node = this.card.querySelector(selector);
        if (node) {
          node.textContent = mapping[selector] || '';
        }
      });
      this.populateForm(purchase.form || {});
      const notesList = this.card.querySelector('[data-management-notes]');
      if (notesList) {
        notesList.innerHTML = '';
        if (purchase.notes && purchase.notes.length) {
          purchase.notes.forEach((note) => {
            const li = document.createElement('li');
            li.className = 'rounded-2xl border border-slate-100 bg-white/80 px-3 py-2 shadow-inner shadow-slate-50';
            li.textContent = note;
            notesList.appendChild(li);
          });
        }
      }
      if (this.modifyButton) {
        this.modifyButton.disabled = !payload.allow_modification;
      }
      if (this.finalizeButton) {
        this.finalizeButton.disabled = !payload.allow_finalize;
      }
      this.toggleShippingFields();
      this.toggleBankPanel();
    }

    handleModify() {
      if (!this.noteInput || !this.card.getAttribute('data-request-modify-url')) {
        return;
      }
      const reason = this.noteInput.value.trim();
      this.sendAction(this.card.getAttribute('data-request-modify-url'), { reason }, 'modify');
    }

    handleFinalize() {
      const finalizeUrl = this.card.getAttribute('data-finalize-url');
      if (!finalizeUrl) {
        this.showFeedback('No encontramos la ruta para finalizar la gestión.', 'error');
        return;
      }
      if (!this.form || !this.orderUrl) {
        this.showFeedback('Completa los datos de la compra antes de finalizar.', 'error');
        return;
      }
      this.displayFieldErrors({});
      const payload = this.collectOrderPayload('confirm_order');
      this.sendOrderUpdate(payload, this.finalizeButton, { silentSuccess: true }).then((saved) => {
        if (!saved) {
          return;
        }
        this.sendAction(finalizeUrl, {}, 'finalize');
      });
    }

    handleSave(intent, button) {
      if (!this.form || !this.orderUrl) {
        this.showFeedback('No encontramos la ruta para guardar la gestión.', 'error');
        return;
      }
      this.displayFieldErrors({});
      const payload = this.collectOrderPayload(intent || 'save_order');
      this.sendOrderUpdate(payload, button || null);
    }

    handleDeliveryChange() {
      this.toggleShippingFields();
    }

    handlePaymentMethodChange() {
      this.toggleBankPanel();
    }

    collectOrderPayload(intent) {
      const getValue = (name) => {
        const field = this.form ? this.form.querySelector(`[data-management-field="${name}"]`) : null;
        if (!field) {
          return '';
        }
        return field.value ? field.value.trim() : '';
      };
      return {
        intent: intent || 'save_order',
        purchase_date: getValue('purchase_date'),
        payment_condition: getValue('payment_condition'),
        payment_method: getValue('payment_method'),
        delivery_condition: getValue('delivery_condition'),
        shipping_eta: getValue('shipping_eta'),
        shipping_notes: getValue('shipping_notes'),
        supplier_account_holder_name: getValue('supplier_account_holder_name'),
        supplier_account_holder_id: getValue('supplier_account_holder_id'),
        supplier_account_type: getValue('supplier_account_type'),
        supplier_account_number: getValue('supplier_account_number'),
        supplier_bank_name: getValue('supplier_bank_name'),
      };
    }

    sendOrderUpdate(payload, button, options) {
      const { silentSuccess = false } = options || {};
      if (!this.orderUrl) {
        this.showFeedback('No encontramos la ruta para guardar la gestión.', 'error');
        return Promise.resolve(false);
      }
      if (button) {
        button.disabled = true;
      }
      return fetch(this.orderUrl, {
        method: 'POST',
        credentials: 'include',
        headers: Object.assign(
          { 'Content-Type': 'application/json' },
          this.csrfToken ? { 'X-CSRFToken': this.csrfToken } : {}
        ),
        body: JSON.stringify(payload),
      })
        .then((response) => response.json().catch(() => ({})).then((data) => ({ ok: response.ok, data })))
        .then((result) => {
          if (!result.ok || (result.data && result.data.error)) {
            const message =
              (result.data && result.data.error) ||
              'No pudimos guardar la información. Intenta nuevamente.';
            this.showFeedback(message, 'error');
            if (result.data && result.data.field_errors) {
              this.displayFieldErrors(result.data.field_errors);
            }
            return false;
          }
          const data = result.data || {};
          if (data.message && !silentSuccess) {
            this.showFeedback(data.message, 'success');
          }
          this.displayFieldErrors({});
          if (typeof this.helpers.onRequestsUpdated === 'function' && data.requests) {
            this.helpers.onRequestsUpdated(data.requests);
          }
          if (typeof this.helpers.onManagementUpdated === 'function' && data.management) {
            this.helpers.onManagementUpdated(data.management);
          } else if (data.management) {
            this.refresh(data.management);
          }
          if (typeof this.helpers.onApprovalsUpdated === 'function') {
            this.helpers.onApprovalsUpdated(data.approvals || null);
          }
          return true;
        })
        .catch(() => {
          this.showFeedback('No pudimos contactar al servidor. Intenta más tarde.', 'error');
          return false;
        })
        .finally(() => {
          if (button) {
            button.disabled = false;
          }
        });
    }

    sendAction(url, payload, action) {
      if (!url) {
        this.showFeedback('No encontramos la ruta para esta acción.', 'error');
        return;
      }
      const targetButton = action === 'modify' ? this.modifyButton : this.finalizeButton;
      if (targetButton) {
        targetButton.disabled = true;
      }
      fetch(url, {
        method: 'POST',
        credentials: 'include',
        headers: Object.assign(
          { 'Content-Type': 'application/json' },
          this.csrfToken ? { 'X-CSRFToken': this.csrfToken } : {}
        ),
        body: JSON.stringify(payload),
      })
        .then((response) => response.json().catch(() => ({})).then((data) => ({ ok: response.ok, data })))
        .then((result) => {
          if (!result.ok || (result.data && result.data.error)) {
            const message =
              (result.data && result.data.error) ||
              'No pudimos ejecutar la acción. Intenta de nuevo.';
            this.showFeedback(message, 'error');
            return;
          }
          const data = result.data || {};
          if (data.message) {
            this.showFeedback(data.message, 'success');
          }
          if (action === 'modify' && this.noteInput) {
            this.noteInput.value = '';
          }
          if (typeof this.helpers.onRequestsUpdated === 'function' && data.requests) {
            this.helpers.onRequestsUpdated(data.requests);
          }
          if (typeof this.helpers.onManagementUpdated === 'function' && data.management) {
            this.helpers.onManagementUpdated(data.management);
          } else if (data.management) {
            this.refresh(data.management);
          }
          if (typeof this.helpers.onApprovalsUpdated === 'function') {
            this.helpers.onApprovalsUpdated(data.approvals || null);
          }
        })
        .catch(() => {
          this.showFeedback('No pudimos contactar al servidor. Intenta más tarde.', 'error');
        })
        .finally(() => {
          if (targetButton) {
            targetButton.disabled = false;
          }
        });
    }

    showFeedback(message, tone) {
      if (!this.feedbackNode) {
        return;
      }
      this.feedbackNode.textContent = message;
      this.feedbackNode.classList.remove('hidden', 'text-emerald-600', 'text-rose-600');
      this.feedbackNode.classList.add(tone === 'success' ? 'text-emerald-600' : 'text-rose-600');
    }

    populateForm(formPayload) {
      if (!this.form || !formPayload) {
        return;
      }
      const setValue = (name, value) => {
        const field = this.form.querySelector(`[data-management-field="${name}"]`);
        if (field) {
          field.value = value || '';
        }
      };
      setValue('purchase_date', formPayload.purchase_date || '');
      setValue('payment_condition', formPayload.payment_condition || '');
      setValue('payment_method', formPayload.payment_method || '');
      setValue('delivery_condition', formPayload.delivery_condition || '');
      setValue('shipping_eta', formPayload.shipping_eta || '');
      setValue('shipping_notes', formPayload.shipping_notes || '');
      setValue('supplier_account_holder_name', formPayload.supplier_account_holder_name || '');
      setValue('supplier_account_holder_id', formPayload.supplier_account_holder_id || '');
      setValue('supplier_account_type', formPayload.supplier_account_type || '');
      setValue('supplier_account_number', formPayload.supplier_account_number || '');
      setValue('supplier_bank_name', formPayload.supplier_bank_name || '');
    }

    displayFieldErrors(errors) {
      if (!this.form) {
        return;
      }
      const nodes = this.form.querySelectorAll('[data-management-error]');
      nodes.forEach((node) => {
        node.classList.add('hidden');
        node.textContent = '';
      });
      if (!errors) {
        return;
      }
      Object.keys(errors).forEach((key) => {
        const node = this.form.querySelector(`[data-management-error="${key}"]`);
        if (node) {
          node.textContent = errors[key][0] || '';
          node.classList.remove('hidden');
        }
      });
    }

    toggleShippingFields() {
      if (!this.shippingFields) {
        return;
      }
      const shouldShow =
        this.deliveryField && this.deliveryField.value === 'shipping';
      this.shippingFields.toggleAttribute('hidden', !shouldShow);
      this.shippingFields.style.display = shouldShow ? '' : 'none';
    }

    toggleBankPanel() {
      if (!this.bankPanel) {
        return;
      }
      const requiresBank =
        this.paymentMethodField && this.paymentMethodField.value === 'transferencia';
      this.bankPanel.toggleAttribute('hidden', !requiresBank);
      this.bankPanel.style.display = requiresBank ? '' : 'none';
      if (requiresBank) {
        this.bankPanel.open = true;
      } else {
        this.bankPanel.removeAttribute('open');
      }
    }
  }

  class PurchaseRequestDraftCard {
    constructor(node, options) {
      const config = options || {};
      this.node = node;
      this.composer = config.composer || {};
      this.csrfToken = config.csrfToken || null;
      this.itemTemplate = config.itemTemplate || null;
      this.callbacks = config.callbacks || {};
      this.form = node.querySelector('[data-purchase-compose-form]');
      this.titleNode = node.querySelector('[data-compose-title]');
      this.subtitleNode = node.querySelector('[data-compose-subtitle]');
      this.referenceNode = node.querySelector('[data-compose-reference]');
      this.summaryInput = this.form ? this.form.querySelector('[data-compose-field="summary"]') : null;
      this.categorySelect = this.form ? this.form.querySelector('[data-compose-field="expense_type_id"]') : null;
      this.scopeSelect = this.form ? this.form.querySelector('[data-compose-field="scope"]') : null;
      this.managerSelect = this.form ? this.form.querySelector('[data-compose-field="assigned_manager_id"]') : null;
      this.addItemButton = this.form ? this.form.querySelector('[data-compose-add-item]') : null;
      this.itemsContainer = this.form ? this.form.querySelector('[data-compose-items-container]') : null;
      this.totalNode = this.form ? this.form.querySelector('[data-compose-total]') : null;
      this.feedbackNode = this.form ? this.form.querySelector('[data-compose-feedback]') : null;
      this.closeButton = node.querySelector('[data-compose-close]');
      this.supplierSelect = this.form ? this.form.querySelector('[data-compose-field="supplier_id"]') : null;
      this.areaFieldWrappers = this.form
        ? Array.prototype.slice.call(this.form.querySelectorAll('[data-area-field]'))
        : [];
      this.areaFieldNodes = {};
      this.areaInputs = {};
      this.areaFieldWrappers.forEach((wrapper) => {
        const key = wrapper.getAttribute('data-area-field');
        if (!key) {
          return;
        }
        const field =
          wrapper.querySelector('[data-area-input]') ||
          wrapper.querySelector('select') ||
          wrapper.querySelector('input');
        this.areaFieldNodes[key] = wrapper;
        this.areaInputs[key] = field;
      });
      this.actionButtons = this.form
        ? Array.prototype.slice.call(this.form.querySelectorAll('[data-compose-action]'))
        : [];
      this.submitUrl = this.composer.submit_url || '';
      this.maxItems = Number(this.composer.max_items || 0) || 10;
      this.currentPurchaseId = null;
      this.boundHandleScopeChange = this.handleScopeChange.bind(this);
    }

    init(initialData) {
      if (!this.form) {
        return;
      }
      this.populateStaticOptions();
      this.bindEvents();
      this.resetForm(initialData || null);
    }

    refreshComposerData(composerData) {
      this.composer = composerData || this.composer;
      this.populateStaticOptions();
      this.renderSupplierSuggestions();
      if (this.composer && this.composer.max_items) {
        this.maxItems = Number(this.composer.max_items) || this.maxItems;
      }
    }

    populateStaticOptions() {
      const includePlaceholder = { includePlaceholder: true };
      this.fillSelect(this.categorySelect, this.composer.categories || [], includePlaceholder);
      this.fillSelect(this.scopeSelect, this.composer.area_scopes || [], includePlaceholder);
      this.fillSelect(this.managerSelect, this.composer.manager_options || [], includePlaceholder);
      this.fillSelect(this.supplierSelect, this.composer.supplier_suggestions || [], includePlaceholder);
      if (this.areaInputs.farm_id) {
        this.fillSelect(this.areaInputs.farm_id, this.composer.farms || [], includePlaceholder);
      }
      this.renderHouseOptions({
        scope: this.scopeSelect ? this.scopeSelect.value : '',
        preserveValue: true,
      });
    }

    fillSelect(select, options, config) {
      if (!select) {
        return;
      }
      const settings = Object.assign(
        {
          includePlaceholder: false,
          preserveValue: true,
          fallbackValue: '',
          resetOnMissing: false,
        },
        config || {}
      );
      const previousValue = settings.preserveValue ? select.value : '';
      const normalizedPrev = previousValue != null ? String(previousValue) : '';
      const fragment = document.createDocumentFragment();
      if (settings.includePlaceholder) {
        const placeholder = document.createElement('option');
        placeholder.value = '';
        placeholder.textContent = settings.placeholder || 'Selecciona una opción';
        fragment.appendChild(placeholder);
      }
      options.forEach((option) => {
        const opt = document.createElement('option');
        opt.value = option.id != null ? option.id : option.value || '';
        opt.textContent = option.label || option.name || '';
        if (option.support_document_type_id) {
          opt.dataset.supportTypeId = option.support_document_type_id;
        }
        fragment.appendChild(opt);
      });
      select.innerHTML = '';
      select.appendChild(fragment);
      if (settings.preserveValue) {
        if (normalizedPrev) {
          select.value = normalizedPrev;
          if (select.value !== normalizedPrev) {
            if (settings.fallbackValue !== undefined) {
              select.value = settings.fallbackValue;
            } else if (settings.resetOnMissing) {
              select.selectedIndex = 0;
            }
          }
        } else {
          select.value = '';
        }
      } else if (settings.defaultValue !== undefined) {
        select.value = settings.defaultValue;
      }
    }

    bindEvents() {
      if (this.closeButton) {
        this.closeButton.addEventListener('click', () => this.close());
      }
      if (this.scopeSelect) {
        this.scopeSelect.addEventListener('change', this.boundHandleScopeChange);
      }
      if (this.areaInputs.farm_id) {
        this.areaInputs.farm_id.addEventListener('change', () =>
          this.renderHouseOptions({
            farmId: this.areaInputs.farm_id.value || null,
            preserveValue: false,
          })
        );
      }
      if (this.addItemButton) {
        this.addItemButton.addEventListener('click', () => this.addItemRow());
      }
      this.actionButtons.forEach((button) => {
        button.addEventListener('click', () => {
          const intent = button.getAttribute('data-compose-action') || 'save_draft';
          this.submit(intent, button);
        });
      });
    }

    resetForm(initialData) {
      const defaults = this.composer.defaults || {};
      this.currentPurchaseId = initialData && initialData.purchase_id ? initialData.purchase_id : null;
      if (this.titleNode) {
        this.titleNode.textContent = this.currentPurchaseId ? 'Editar solicitud' : 'Nueva solicitud';
      }
      if (this.subtitleNode) {
        this.subtitleNode.textContent = this.currentPurchaseId
          ? 'Actualiza la información y vuelve a enviar la solicitud.'
          : 'Captura el requerimiento, agrega los ítems y envía a aprobación.';
      }
      if (this.referenceNode) {
        this.referenceNode.textContent = initialData && initialData.code ? initialData.code : '';
      }
      if (this.summaryInput) {
        this.summaryInput.value = (initialData && initialData.name) || defaults.summary || '';
      }
      if (this.categorySelect) {
        this.categorySelect.value = (initialData && initialData.expense_type_id) || '';
      }
      if (this.managerSelect) {
        this.managerSelect.value = (initialData && initialData.assigned_manager_id) || defaults.assigned_manager_id || '';
      }
      const areaPayload = (initialData && initialData.area) || defaults.area || {};
      if (this.scopeSelect) {
        const defaultScope = defaults.area && defaults.area.scope ? defaults.area.scope : '';
        this.scopeSelect.value = areaPayload.scope || defaultScope || '';
        this.handleScopeChange();
      }
      if (this.areaInputs.farm_id) {
        this.areaInputs.farm_id.value = areaPayload.farm_id || '';
      }
      this.renderHouseOptions({
        scope: this.scopeSelect ? this.scopeSelect.value : '',
        farmId: areaPayload.farm_id || null,
        preserveValue: false,
      });
      if (this.areaInputs.chicken_house_id) {
        this.areaInputs.chicken_house_id.value = areaPayload.chicken_house_id || '';
      }
      this.setSupplier(initialData && initialData.supplier ? initialData.supplier : null);
      this.resetItems(initialData && initialData.items ? initialData.items : null);
      this.clearFieldErrors();
      this.showFeedback('');
    }

    resetItems(items) {
      if (!this.itemsContainer) {
        return;
      }
      this.itemsContainer.innerHTML = '';
      const initialItems = Array.isArray(items) && items.length ? items : [null];
      initialItems.forEach((item) => this.addItemRow(item));
      this.updateTotals();
    }

    setSupplier(option) {
      if (!this.supplierSelect) {
        return;
      }
      const value = option && option.id ? option.id : '';
      this.supplierSelect.value = value;
    }

    addItemRow(initialItem) {
      if (!this.itemsContainer || !this.itemTemplate) {
        return;
      }
      if (this.maxItems && this.itemsContainer.children.length >= this.maxItems) {
        return;
      }
      const fragment = document.importNode(this.itemTemplate.content, true);
      const row = fragment.querySelector('[data-compose-item-row]');
      if (!row) {
        return;
      }
      const idInput = row.querySelector('input[data-item-field="id"]');
      const descriptionInput = row.querySelector('input[data-item-field="description"]');
      const quantityInput = row.querySelector('input[data-item-field="quantity"]');
      const valueInput = row.querySelector('input[data-item-field="unit_value"]');
      const subtotalNode = row.querySelector('[data-item-subtotal]');
      const removeButton = row.querySelector('[data-compose-remove-item]');
      if (initialItem) {
        if (idInput) {
          idInput.value = initialItem.id || '';
        }
        if (descriptionInput) {
          descriptionInput.value = initialItem.description || '';
        }
        if (quantityInput) {
          quantityInput.value = initialItem.quantity || initialItem.requested_label || '';
        }
        if (valueInput) {
          valueInput.value = initialItem.unit_value || initialItem.unit_value_label || initialItem.unit_value_display || initialItem.unit_value_input || '';
        }
        if (subtotalNode) {
          subtotalNode.textContent = initialItem.subtotal || initialItem.subtotal_label || '$ 0';
        }
      }
      if (removeButton) {
        removeButton.addEventListener('click', () => {
          row.remove();
          this.updateTotals();
        });
      }
      [descriptionInput, quantityInput, valueInput].forEach((input) => {
        if (!input) {
          return;
        }
        input.addEventListener('input', () => {
          this.updateTotals();
        });
      });
      this.itemsContainer.appendChild(row);
    }

    toggleAreaFieldVisibility(fieldName, shouldShow) {
      const wrapper = this.areaFieldNodes[fieldName];
      if (!wrapper) {
        return;
      }
      wrapper.classList.toggle('hidden', !shouldShow);
      if (typeof wrapper.toggleAttribute === 'function') {
        wrapper.toggleAttribute('hidden', !shouldShow);
      } else if (!shouldShow) {
        wrapper.setAttribute('hidden', 'hidden');
      } else {
        wrapper.removeAttribute('hidden');
      }
      if (!shouldShow && this.areaInputs[fieldName]) {
        const input = this.areaInputs[fieldName];
        if (input.tagName && input.tagName.toLowerCase() === 'select') {
          input.selectedIndex = 0;
        } else if ('value' in input) {
          input.value = '';
        }
      }
    }

    handleScopeChange() {
      const scope = this.scopeSelect ? this.scopeSelect.value : '';
      const showFarm = scope === 'farm';
      const showHouse = scope === 'chicken_house';
      this.toggleAreaFieldVisibility('farm_id', showFarm);
      this.toggleAreaFieldVisibility('chicken_house_id', showHouse);
      const farmFilter = showFarm && this.areaInputs.farm_id ? this.areaInputs.farm_id.value || null : null;
      this.renderHouseOptions({
        scope,
        farmId: farmFilter,
        preserveValue: showHouse,
      });
    }

    renderHouseOptions(config) {
      if (!this.areaInputs.chicken_house_id) {
        return;
      }
      const settings = config || {};
      const scopeValue =
        typeof settings.scope === 'string' && settings.scope
          ? settings.scope
          : this.scopeSelect
            ? this.scopeSelect.value
            : '';
      const farmValue =
        Object.prototype.hasOwnProperty.call(settings, 'farmId') && settings.farmId !== undefined
          ? settings.farmId
          : this.areaInputs.farm_id
            ? this.areaInputs.farm_id.value
            : null;
      const houses = this.composer.chicken_houses || [];
      const shouldFilterByFarm = scopeValue !== 'chicken_house' && farmValue;
      const filtered = shouldFilterByFarm
        ? houses.filter((house) => String(house.farm_id) === String(farmValue))
        : houses;
      const normalized = filtered.map((house) => ({
        id: house.id != null ? house.id : house.value || '',
        label: house.full_label || house.label || house.name || '',
      }));
      const preserveValue = Object.prototype.hasOwnProperty.call(settings, 'preserveValue')
        ? settings.preserveValue
        : true;
      this.fillSelect(this.areaInputs.chicken_house_id, normalized, {
        includePlaceholder: true,
        preserveValue,
        resetOnMissing: true,
      });
    }

    collectPayload(intent) {
      if (!this.form) {
        return null;
      }
      const areaPayload = {
        scope: this.scopeSelect ? this.scopeSelect.value : '',
        farm_id: this.areaInputs.farm_id ? this.areaInputs.farm_id.value : '',
        chicken_house_id: this.areaInputs.chicken_house_id ? this.areaInputs.chicken_house_id.value : '',
      };
      const items = [];
      if (this.itemsContainer) {
        Array.prototype.forEach.call(this.itemsContainer.querySelectorAll('[data-compose-item-row]'), (row) => {
          const getInput = (name) => row.querySelector(`[data-item-field="${name}"]`);
          const toNumber = (input) => {
            if (!input) {
              return '';
            }
            const raw = parseFloat(input.value);
            return Number.isNaN(raw) ? '' : raw;
          };
          const itemPayload = {
            id: getInput('id') ? getInput('id').value || null : null,
            description: getInput('description') ? getInput('description').value.trim() : '',
            quantity: getInput('quantity') ? getInput('quantity').value.trim() : '',
            estimated_amount: getInput('unit_value') ? getInput('unit_value').value.trim() : '',
          };
          const quantityNumber = parseFloat(itemPayload.quantity);
          const valueNumber = parseFloat(itemPayload.estimated_amount);
          if (!Number.isNaN(quantityNumber)) {
            itemPayload.quantity = quantityNumber;
          }
          if (!Number.isNaN(valueNumber)) {
            itemPayload.estimated_amount = valueNumber;
          }
          items.push(itemPayload);
        });
      }
      return {
        intent: intent || 'save_draft',
        purchase_id: this.currentPurchaseId,
        summary: this.summaryInput ? this.summaryInput.value.trim() : '',
        expense_type_id: this.categorySelect ? this.categorySelect.value : '',
        assigned_manager_id: this.managerSelect ? this.managerSelect.value : '',
        supplier_id: this.supplierSelect ? this.supplierSelect.value : '',
        area: areaPayload,
        items,
      };
    }

    submit(intent, button) {
      if (!this.submitUrl || !this.form) {
        this.showFeedback('No encontramos la ruta para registrar la solicitud.', 'error');
        return;
      }
      const payload = this.collectPayload(intent);
      if (!payload) {
        return;
      }
      this.clearFieldErrors();
      this.showFeedback('', 'neutral');
      if (button) {
        button.disabled = true;
      }
      fetch(this.submitUrl, {
        method: 'POST',
        credentials: 'include',
        headers: Object.assign(
          { 'Content-Type': 'application/json' },
          this.csrfToken ? { 'X-CSRFToken': this.csrfToken } : {}
        ),
        body: JSON.stringify(payload),
      })
        .then((response) => response.json().catch(() => ({})).then((data) => ({ ok: response.ok, data })))
        .then((result) => {
          if (!result.ok || (result.data && result.data.error)) {
            const message =
              (result.data && result.data.error) ||
              'No pudimos guardar la solicitud. Revisa la información.';
            this.showFeedback(message, 'error');
            if (result.data) {
              this.displayFieldErrors(result.data.field_errors || {}, result.data.item_errors || {});
            }
            return;
          }
          const data = result.data || {};
          if (data.message) {
            this.showFeedback(data.message, 'success');
          }
          this.currentPurchaseId = data.purchase ? data.purchase.id : null;
          if (data.requests && typeof this.callbacks.onRequestsUpdated === 'function') {
            try {
              this.callbacks.onRequestsUpdated(data.requests);
            } catch (error) {
              console.warn('No se pudo refrescar la lista de solicitudes.', error);
            }
          }
          if (data.composer && typeof this.callbacks.onComposerRefresh === 'function') {
            try {
              this.callbacks.onComposerRefresh(data.composer);
            } catch (error) {
              console.warn('No se pudo actualizar el compositor de compras.', error);
            }
          }
          if (!this.currentPurchaseId || payload.intent === 'send_workflow') {
            this.close();
          }
        })
        .catch(() => {
          this.showFeedback('No pudimos contactar al servidor. Intenta más tarde.', 'error');
        })
        .finally(() => {
          if (button) {
            button.disabled = false;
          }
        });
    }

    displayFieldErrors(fieldErrors, itemErrors) {
      if (!this.form) {
        return;
      }
      const errorMap = fieldErrors || {};
      Object.keys(errorMap).forEach((key) => {
        const target = this.form.querySelector(`[data-compose-error="${key}"]`);
        if (target) {
          target.textContent = errorMap[key][0] || '';
          target.classList.remove('hidden');
        }
      });
      if (itemErrors && this.itemsContainer) {
        Array.prototype.forEach.call(this.itemsContainer.querySelectorAll('[data-compose-item-row]'), (row, index) => {
          const errorsForRow = itemErrors[index];
          if (!errorsForRow) {
            return;
          }
          Object.keys(errorsForRow).forEach((field) => {
            const target = row.querySelector(`[data-item-error="${field}"]`);
            if (target) {
              target.textContent = errorsForRow[field][0] || '';
              target.classList.remove('hidden');
            }
          });
        });
      }
    }

    clearFieldErrors() {
      if (!this.form) {
        return;
      }
      Array.prototype.forEach.call(this.form.querySelectorAll('[data-compose-error]'), (node) => {
        node.classList.add('hidden');
        node.textContent = '';
      });
      if (this.itemsContainer) {
        Array.prototype.forEach.call(this.itemsContainer.querySelectorAll('[data-item-error]'), (node) => {
          node.classList.add('hidden');
          node.textContent = '';
        });
      }
    }

    updateTotals() {
      if (!this.itemsContainer || !this.totalNode) {
        return;
      }
      let total = 0;
      Array.prototype.forEach.call(this.itemsContainer.querySelectorAll('[data-compose-item-row]'), (row) => {
        const quantityInput = row.querySelector('[data-item-field="quantity"]');
        const valueInput = row.querySelector('[data-item-field="unit_value"]');
        const subtotalNode = row.querySelector('[data-item-subtotal]');
        const quantity = quantityInput ? parseFloat(quantityInput.value) : 0;
        const unitValue = valueInput ? parseFloat(valueInput.value) : 0;
        const subtotal = (Number.isNaN(quantity) ? 0 : quantity) * (Number.isNaN(unitValue) ? 0 : unitValue);
        if (subtotalNode) {
          subtotalNode.textContent = formatCurrency(subtotal, '$');
        }
        total += subtotal;
      });
      this.totalNode.textContent = formatCurrency(total, '$');
    }

    showFeedback(message, tone) {
      if (!this.feedbackNode) {
        return;
      }
      this.feedbackNode.textContent = message || '';
      this.feedbackNode.classList.toggle('hidden', !message);
      this.feedbackNode.classList.remove('text-rose-600', 'text-emerald-600', 'text-slate-500');
      if (!message) {
        return;
      }
      if (tone === 'success') {
        this.feedbackNode.classList.add('text-emerald-600');
      } else if (tone === 'error') {
        this.feedbackNode.classList.add('text-rose-600');
      } else {
        this.feedbackNode.classList.add('text-slate-500');
      }
    }

    close() {
      if (typeof this.callbacks.onClose === 'function') {
        this.callbacks.onClose(this);
      }
      this.node.remove();
    }
  }

  class PurchaseRequestComposerController {
    constructor(root, options) {
      this.root = root;
      const config = options || {};
      this.csrfToken = config.csrfToken || null;
      this.callbacks = config.callbacks || {};
      this.container = root.querySelector('[data-purchase-compose-container]');
      this.template = root.querySelector('template[data-purchase-compose-template]');
      this.itemTemplate = root.querySelector('template[data-purchase-compose-item-template]');
      const scriptId = root.getAttribute('data-composer-source');
      if (scriptId) {
        const scriptElement = document.getElementById(scriptId);
        const scriptContent = scriptElement ? scriptElement.textContent || '' : '';
        this.composerData = safeParseJSON(scriptContent);
      } else {
        this.composerData = null;
      }
      this.cards = new Set();
      this.launchButtons = document.querySelectorAll('[data-purchase-request-launch]');
      this.boundLaunchHandler = this.handleLaunchRequest.bind(this);
      this.delegateBound = false;
    }

    init(payload) {
      if (payload) {
        this.setComposerData(payload);
      }
      this.bindLaunchers();
    }

    setComposerData(payload) {
      this.composerData = payload || this.composerData;
      const tmMiniApp = window.tmMiniApp || (window.tmMiniApp = {});
      tmMiniApp.purchases = tmMiniApp.purchases || {};
      tmMiniApp.purchases.composer = this.composerData;
      this.cards.forEach((cardInstance) => {
        cardInstance.refreshComposerData(this.composerData);
      });
    }

    bindLaunchers() {
      if (this.launchButtons && this.launchButtons.length) {
        this.launchButtons.forEach((button) => {
          button.addEventListener('click', this.boundLaunchHandler);
        });
      }
      if (!this.delegateBound) {
        document.addEventListener('click', this.boundLaunchHandler);
        this.delegateBound = true;
      }
    }

    handleLaunchRequest(event) {
      const trigger = event && event.target ? event.target.closest('[data-purchase-request-launch]') : null;
      if (!trigger) {
        return;
      }
      if (typeof event.preventDefault === 'function') {
        event.preventDefault();
      }
      if (typeof event.stopPropagation === 'function') {
        event.stopPropagation();
      }
      this.launch();
    }

    launch(initialData) {
      if (!this.container || !this.template) {
        return;
      }
      const fragment = document.importNode(this.template.content, true);
      const cardNode = fragment.querySelector('[data-purchase-compose-card]');
      if (!cardNode) {
        return;
      }
      this.container.appendChild(fragment);
      const card = new PurchaseRequestDraftCard(cardNode, {
        composer: this.composerData || {},
        csrfToken: this.csrfToken,
        itemTemplate: this.itemTemplate,
        callbacks: {
          onClose: () => this.cards.delete(card),
          onRequestsUpdated: (payload) => {
            if (typeof this.callbacks.onRequestsUpdated === 'function') {
              this.callbacks.onRequestsUpdated(payload);
            }
          },
          onComposerRefresh: (payload) => {
            this.setComposerData(payload);
          },
        },
      });
      this.cards.add(card);
      card.init(initialData || null);
    }
  }

  function reorderPurchaseCards() {
    const container = document.querySelector('#tm-purchases .space-y-4');
    if (!container) {
      return;
    }
    const cardOrder = [
      '[data-purchase-approval-card]',
      '[data-purchase-management-card]',
      '[data-purchase-requests-card]',
    ];
    const fragment = document.createDocumentFragment();
    let moved = false;
    cardOrder.forEach((selector) => {
      const card = container.querySelector(selector);
      if (card) {
        fragment.appendChild(card);
        moved = true;
      }
    });
    if (moved) {
      container.appendChild(fragment);
    }
  }

  function initPurchaseControllers() {
    reorderPurchaseCards();
    const purchasesPayload = tmMiniApp.purchases || {};
    const controllers = (tmMiniApp.purchasesControllers = tmMiniApp.purchasesControllers || {});
    const csrfToken = tmMiniApp.csrfToken || null;
    const notifyRequestsUpdated = (payload) => {
      if (controllers.requestsList && payload) {
        controllers.requestsList.render(payload);
      }
    };
    const notifyManagementUpdated = (payload) => {
      if (controllers.management && payload) {
        controllers.management.refresh(payload);
      }
    };
    const notifyApprovalsUpdated = (payload) => {
      if (controllers.approvals) {
        controllers.approvals.refresh(payload || null);
      }
    };

    const composerRoot = document.querySelector('[data-purchase-compose-root]');
    if (composerRoot) {
      controllers.composer = new PurchaseRequestComposerController(composerRoot, {
        csrfToken,
        callbacks: {
          onRequestsUpdated: notifyRequestsUpdated,
        },
      });
      controllers.composer.init(purchasesPayload.composer || null);
      tmMiniApp.launchPurchaseComposer = (initialData) => {
        if (controllers.composer) {
          controllers.composer.launch(initialData || null);
        }
      };
    }

    const requestsCard = document.querySelector('[data-purchase-requests-card]');
    if (requestsCard) {
      controllers.requestsList = new PurchaseRequestsListController(requestsCard, {
        onEditRequest: (payload) => {
          if (controllers.composer) {
            controllers.composer.launch(payload || null);
          }
        },
      });
      controllers.requestsList.render(purchasesPayload.overview);
    }

    const approvalsCard = document.querySelector('[data-purchase-approval-card]');
    if (approvalsCard) {
      controllers.approvals = new PurchaseApprovalCardController(approvalsCard, csrfToken, {
        onRequestsUpdated: notifyRequestsUpdated,
        onManagementUpdated: notifyManagementUpdated,
        onApprovalsUpdated: notifyApprovalsUpdated,
      });
      controllers.approvals.init(purchasesPayload.approvals || null);
    }

    const managementCard = document.querySelector('[data-purchase-management-card]');
    if (managementCard) {
      controllers.management = new PurchaseManagementCardController(managementCard, csrfToken, {
        onRequestsUpdated: notifyRequestsUpdated,
        onManagementUpdated: notifyManagementUpdated,
        onApprovalsUpdated: notifyApprovalsUpdated,
      });
      controllers.management.init();
      controllers.management.refresh(purchasesPayload.management);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initPurchaseControllers);
  } else {
    initPurchaseControllers();
  }
})();

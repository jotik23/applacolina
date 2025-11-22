(() => {
  'use strict';

  const ROOM_FIELDS = ['production', 'consumption', 'mortality', 'discard'];
  // These fields are optional in the mini app; keep the set for future required fields.
  const REQUIRED_ROOM_FIELDS = new Set();
  const INTEGER_PATTERN = /^[0-9]+$/;

  const ROOM_FIELD_LABELS = {
    production: 'Producción',
    consumption: 'Consumo',
    mortality: 'Mortalidad',
    discard: 'Descarte',
  };

  const BUTTON_LABELS = {
    save: 'Guardar',
    complete: 'Guardar',
  };

  const BUTTON_ICONS = {
    save: '💾',
    complete: '✅',
  };

  const BUTTON_STYLE_GROUPS = {
    save: ['bg-rose-600', 'hover:bg-rose-700', 'focus:ring-rose-300/60', 'border-rose-200', 'shadow-rose-200/70'],
    complete: [
      'bg-emerald-600',
      'hover:bg-emerald-700',
      'focus:ring-emerald-300/60',
      'border-emerald-200',
      'shadow-emerald-200/70',
    ],
  };

  class ProductionCardController {
    constructor(card, helpers) {
      this.card = card;
      this.helpers = helpers || {};
      this.submitUrl = card.getAttribute('data-production-submit-url') || '';
      this.productionDate = card.getAttribute('data-production-date') || null;
      this.cardTitle = card.getAttribute('data-task-title') || '';
      this.submitButton = card.querySelector('[data-production-submit-button]');
      this.successBanner = card.querySelector('[data-production-complete-banner]');
      this.saveInFlight = false;
      this.csrfToken = this.helpers.csrfToken || null;
      this.telegram = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
      this.numberFormatter =
        typeof Intl !== 'undefined' && typeof Intl.NumberFormat === 'function'
          ? new Intl.NumberFormat('es-CO', { maximumFractionDigits: 0 })
          : null;

      this.handleSubmit = this.handleSubmit.bind(this);
      this.refreshButtonState = this.refreshButtonState.bind(this);
    }

    init() {
      this.lotNodes = this.getLotNodes();
      this.bindLots();
      if (this.submitButton) {
        this.submitButtonBaseClasses = this.submitButton.className;
        this.submitButton.addEventListener('click', this.handleSubmit);
      }
      this.refreshButtonState();

      if (this.helpers) {
        this.helpers.refreshProductionButtonState = () => this.refreshButtonState();
        this.helpers.productionCard = this;
        this.helpers.submitProduction = (button) => this.submitFromTaskAction(button);
      }
    }

    getLotNodes() {
      return Array.prototype.slice.call(this.card.querySelectorAll('[data-production-lot]'));
    }

    bindLots() {
      this.lotNodes.forEach((lotNode) => {
        const roomInputs = Array.prototype.slice.call(lotNode.querySelectorAll('[data-production-room-field]'));
        roomInputs.forEach((input) => {
          input.addEventListener('input', () => {
            input.classList.remove('border-rose-300');
            this.recalculateLotTotals(lotNode);
            this.refreshButtonState();
          });
        });

        const averageInput = lotNode.querySelector('[data-production-average]');
        if (averageInput) {
          averageInput.addEventListener('input', () => {
            averageInput.classList.remove('border-rose-300');
            this.refreshButtonState();
          });
        }

        this.recalculateLotTotals(lotNode);
      });
    }

    handleSubmit(event) {
      event.preventDefault();
      if (this.saveInFlight) {
        return;
      }
      if (!this.submitUrl) {
        this.showFeedback('No encontramos la ruta para guardar los registros.', 'error');
        return;
      }
      if (!this.lotNodes.length) {
        this.showFeedback('No hay lotes activos para registrar.', 'error');
        return;
      }

      const entries = [];
      for (let index = 0; index < this.lotNodes.length; index += 1) {
        const entry = this.collectLotEntry(this.lotNodes[index]);
        if (!entry) {
          return;
        }
        entries.push(entry);
      }

      const payload = {
        date: this.productionDate,
        lots: entries,
      };

      this.persist(payload);
    }

    submitFromTaskAction(button) {
      if (this.saveInFlight) {
        return;
      }
      const fakeEvent = {
        preventDefault() {},
        currentTarget: button || this.submitButton,
      };
      this.handleSubmit(fakeEvent);
    }

    async persist(payload) {
      if (!this.submitButton) {
        return;
      }

      this.saveInFlight = true;
      this.refreshButtonState();
      this.setButtonLoading(true);

      try {
        const response = await fetch(this.submitUrl, {
          method: 'POST',
          credentials: 'include',
          headers: Object.assign(
            { 'Content-Type': 'application/json' },
            this.csrfToken ? { 'X-CSRFToken': this.csrfToken } : {}
          ),
          body: JSON.stringify(payload),
        });
        const result = await this.parseJsonResponse(response);
        if (result.status === 401 || result.status === 403) {
          this.redirectToLogin();
          return;
        }
        if (!result.ok || (result.data && result.data.error)) {
          const message =
            (result.data && result.data.error) || 'No fue posible guardar los registros. Intenta de nuevo.';
          this.showFeedback(message, 'error');
          this.triggerHaptic('error');
          return;
        }

        const data = result.data || {};
        if (data.production) {
          this.updateFromResponse(data.production);
          this.updateCompletionState(Boolean(data.production.has_records));
        }
        this.showFeedback('Registros guardados correctamente.', 'success');
        this.sendTaskAction('complete-production', this.cardTitle, { section: 'production' });
        this.triggerHaptic('success');
      } catch (error) {
        console.warn('No se pudo guardar el registro de producción:', error);
        this.showFeedback('No fue posible guardar los registros. Intenta de nuevo.', 'error');
        this.triggerHaptic('error');
      } finally {
        this.saveInFlight = false;
        this.setButtonLoading(false);
        this.refreshButtonState();
      }
    }

    updateFromResponse(payload) {
      if (!payload) {
        return;
      }

      if (payload.submit_url) {
        this.submitUrl = payload.submit_url;
        this.card.setAttribute('data-production-submit-url', this.submitUrl);
      }
      if (payload.date) {
        this.productionDate = payload.date;
        this.card.setAttribute('data-production-date', this.productionDate);
      }

      this.lotNodes = this.getLotNodes();
      const lotMap = new Map();
      this.lotNodes.forEach((lotNode) => {
        const batchId = lotNode.getAttribute('data-batch-id');
        if (batchId) {
          lotMap.set(String(batchId), lotNode);
        }
      });

      const lotsPayload = Array.isArray(payload.lots) ? payload.lots : [];
      lotsPayload.forEach((lotPayload) => {
        const target = lotMap.get(String(lotPayload.id));
        if (!target) {
          return;
        }
        this.populateLotFromPayload(target, lotPayload);
      });

      this.refreshButtonState();
    }

    populateLotFromPayload(lotNode, lotPayload) {
      if (!lotNode) {
        return;
      }
      const roomNodes = Array.prototype.slice.call(lotNode.querySelectorAll('[data-production-room]'));
      const roomMap = new Map();
      roomNodes.forEach((node) => {
        const roomId = node.getAttribute('data-room-id');
        if (roomId) {
          roomMap.set(String(roomId), node);
        }
      });

      const roomsPayload = Array.isArray(lotPayload.rooms) ? lotPayload.rooms : [];
      roomsPayload.forEach((roomPayload) => {
        const roomNode = roomMap.get(String(roomPayload.id));
        if (!roomNode) {
          return;
        }
        ROOM_FIELDS.forEach((field) => {
          const input = roomNode.querySelector('[data-production-room-field="' + field + '"]');
          if (!input) {
            return;
          }
          const value =
            roomPayload && Object.prototype.hasOwnProperty.call(roomPayload, field) ? roomPayload[field] : '';
          input.value = this.formatValueForInput(value);
          input.classList.remove('border-rose-300');
        });
      });

      const averageInput = lotNode.querySelector('[data-production-average]');
      if (averageInput) {
        const record = lotPayload.record || null;
        const weight = record && Object.prototype.hasOwnProperty.call(record, 'average_egg_weight')
          ? record.average_egg_weight
          : null;
        averageInput.value = this.formatValueForInput(weight);
        averageInput.classList.remove('border-rose-300');
      }

      this.updateLastUpdateSection(lotNode, lotPayload.record || null);
      this.recalculateLotTotals(lotNode);
    }

    updateLastUpdateSection(lotNode, record) {
      const container = lotNode.querySelector('[data-production-last]');
      if (!container) {
        return;
      }
      if (record && record.last_updated_label) {
        const label = record.last_updated_label;
        const actor = record.last_actor || null;
        container.innerHTML = '';
        container.classList.remove('text-rose-500');

        container.appendChild(document.createTextNode('Última actualización: '));
        const labelSpan = document.createElement('span');
        labelSpan.setAttribute('data-production-last-label', '');
        labelSpan.textContent = label;
        container.appendChild(labelSpan);

        if (actor) {
          container.appendChild(document.createTextNode(' · '));
          const actorSpan = document.createElement('span');
          actorSpan.setAttribute('data-production-last-user', '');
          actorSpan.textContent = actor;
          container.appendChild(actorSpan);
        }
      } else {
        container.textContent = 'Sin registros previos para este lote.';
        container.classList.add('text-rose-500');
      }
    }

    collectLotEntry(lotNode) {
      if (!lotNode) {
        return null;
      }
      const batchId = lotNode.getAttribute('data-batch-id');
      if (!batchId) {
        this.showFeedback('No se pudo identificar el lote para registrar.', 'error');
        return null;
      }

      const numericBatch = parseInt(batchId, 10);
      const entry = {
        bird_batch: Number.isFinite(numericBatch) ? numericBatch : batchId,
      };

      const lotLabelNode = lotNode.querySelector('h4');
      const lotLabel = lotLabelNode ? lotLabelNode.textContent.trim() : '';

      const roomNodes = Array.prototype.slice.call(lotNode.querySelectorAll('[data-production-room]'));
      if (!roomNodes.length) {
        this.showFeedback('No encontramos salones asignados para este lote.', 'error');
        return null;
      }

      const roomEntries = [];
      for (let index = 0; index < roomNodes.length; index += 1) {
        const roomNode = roomNodes[index];
        const roomIdRaw = roomNode.getAttribute('data-room-id');
        const roomLabel = roomNode.getAttribute('data-room-label') || '';
        const parsedRoomId = parseInt(roomIdRaw || '', 10);
        if (!roomIdRaw || !Number.isFinite(parsedRoomId)) {
          this.showFeedback('No se pudo identificar un salón del lote.', 'error');
          return null;
        }
        const roomEntry = { room_id: parsedRoomId };
        let hasError = false;

        for (let fieldIndex = 0; fieldIndex < ROOM_FIELDS.length; fieldIndex += 1) {
          const field = ROOM_FIELDS[fieldIndex];
          const input = roomNode.querySelector('[data-production-room-field="' + field + '"]');
          if (!input) {
            continue;
          }
          const value = typeof input.value === 'string' ? input.value.trim() : '';
          const isRequired = REQUIRED_ROOM_FIELDS.has(field);
          if (!value) {
            if (isRequired) {
              const label = ROOM_FIELD_LABELS[field] || 'este campo';
              const message = roomLabel
                ? 'Completa ' + label + ' para ' + roomLabel + (lotLabel ? ' (' + lotLabel + ')' : '') + '.'
                : 'Completa ' + label + ' antes de guardar.';
              this.showFeedback(message, 'error');
              input.classList.add('border-rose-300');
              if (typeof input.focus === 'function') {
                input.focus();
              }
              hasError = true;
              break;
            } else {
              roomEntry[field] = '0';
            }
          } else {
            if (!INTEGER_PATTERN.test(value)) {
              const label = ROOM_FIELD_LABELS[field] || 'este campo';
              const message = roomLabel
                ? 'Usa números enteros para ' +
                  label +
                  ' de ' +
                  roomLabel +
                  (lotLabel ? ' (' + lotLabel + ')' : '') +
                  '.'
                : 'Usa números enteros antes de guardar.';
              this.showFeedback(message, 'error');
              input.classList.add('border-rose-300');
              if (typeof input.focus === 'function') {
                input.focus();
              }
              hasError = true;
              break;
            }
            roomEntry[field] = value;
          }
        }

        if (hasError) {
          return null;
        }

        roomEntries.push(roomEntry);
      }

      const averageInput = lotNode.querySelector('[data-production-average]');
      if (averageInput) {
        const averageValue = averageInput.value && averageInput.value.trim();
        if (averageValue) {
          if (!INTEGER_PATTERN.test(averageValue)) {
            this.showFeedback('El peso promedio debe ser un número entero.', 'error');
            averageInput.classList.add('border-rose-300');
            if (typeof averageInput.focus === 'function') {
              averageInput.focus();
            }
            return null;
          }
          entry.average_egg_weight = averageValue;
        }
      }

      entry.rooms = roomEntries;
      return entry;
    }

    recalculateLotTotals(lotNode) {
      if (!lotNode) {
        return;
      }
      const totals = {
        production: 0,
        consumption: 0,
        mortality: 0,
        discard: 0,
      };

      const roomNodes = Array.prototype.slice.call(lotNode.querySelectorAll('[data-production-room]'));
      roomNodes.forEach((roomNode) => {
        ROOM_FIELDS.forEach((field) => {
          const input = roomNode.querySelector('[data-production-room-field="' + field + '"]');
          if (!input) {
            return;
          }
          const value = typeof input.value === 'string' ? input.value.trim() : '';
          if (!value) {
            return;
          }
          if (!INTEGER_PATTERN.test(value)) {
            return;
          }
          const parsed = parseInt(value, 10);
          if (!Number.isFinite(parsed) || parsed < 0) {
            return;
          }
          if (field === 'production') {
            totals.production += parsed;
          } else {
            totals[field] += parsed;
          }
        });
      });

      const displayTotals = {
        production: totals.production,
        consumption: totals.consumption,
        mortality: totals.mortality,
        discard: totals.discard,
      };

      Object.keys(displayTotals).forEach((field) => {
        const target = lotNode.querySelector('[data-production-total-label="' + field + '"]');
        if (!target) {
          return;
        }
        const value = displayTotals[field];
        if (this.numberFormatter) {
          target.textContent = this.numberFormatter.format(value);
        } else if (field === 'production') {
          const rounded = Math.round(value * 100) / 100;
          target.textContent = Number.isInteger(rounded)
            ? String(rounded)
            : rounded.toFixed(2).replace(/0+$/, '').replace(/\.$/, '');
        } else {
          target.textContent = String(value);
        }
      });
    }

    refreshButtonState() {
      if (!this.submitButton) {
        return;
      }
      const isReady = this.areAllLotsReady();
      const mode = isReady ? 'complete' : 'save';

      if (this.submitButtonBaseClasses) {
        this.submitButton.className = this.submitButtonBaseClasses;
      }
      this.submitButton.dataset.mode = mode;

      const iconNode = this.submitButton.querySelector('[data-production-button-icon]');
      if (iconNode) {
        iconNode.textContent = BUTTON_ICONS[mode] || BUTTON_ICONS.save;
      }
      const labelNode = this.submitButton.querySelector('[data-production-button-label]');
      if (labelNode) {
        labelNode.textContent = BUTTON_LABELS[mode] || BUTTON_LABELS.save;
      }

      const classesToAdd = BUTTON_STYLE_GROUPS[mode] || BUTTON_STYLE_GROUPS.save;
      classesToAdd.forEach((cls) => this.submitButton.classList.add(cls));
      this.submitButton.disabled = this.saveInFlight;
    }

    areAllLotsReady() {
      if (!this.lotNodes.length) {
        return false;
      }
      return this.lotNodes.every((lotNode) => {
        const roomNodes = Array.prototype.slice.call(lotNode.querySelectorAll('[data-production-room]'));
        if (!roomNodes.length) {
          return false;
        }
        return roomNodes.every((roomNode) => {
          return ROOM_FIELDS.every((field) => {
            if (!REQUIRED_ROOM_FIELDS.has(field)) {
              return true;
            }
            const input = roomNode.querySelector('[data-production-room-field="' + field + '"]');
            if (!input) {
              return false;
            }
            const value = input.value && input.value.trim();
            if (!value) {
              return false;
            }
            return INTEGER_PATTERN.test(value);
          });
        });
      });
    }

    updateCompletionState(isComplete) {
      if (!this.card) {
        return;
      }
      if (isComplete) {
        this.card.classList.add('ring-2', 'ring-emerald-300', 'ring-offset-2', 'ring-offset-white');
        this.card.setAttribute('data-task-completed', 'true');
        if (this.successBanner) {
          this.successBanner.classList.remove('hidden');
        }
      } else {
        this.card.classList.remove('ring-2', 'ring-emerald-300', 'ring-offset-2', 'ring-offset-white');
        this.card.setAttribute('data-task-completed', 'false');
        if (this.successBanner) {
          this.successBanner.classList.add('hidden');
        }
      }
    }

    showFeedback(message, tone) {
      const fn = this.helpers && typeof this.helpers.showTaskFeedback === 'function'
        ? this.helpers.showTaskFeedback
        : null;
      if (fn) {
        fn(this.card, message, tone);
      } else {
        const method = tone === 'error' ? 'warn' : 'info';
        console[method](message);
      }
    }

    setButtonLoading(isLoading) {
      if (!this.submitButton) {
        return;
      }
      const fn =
        this.helpers && typeof this.helpers.setButtonLoading === 'function'
          ? this.helpers.setButtonLoading
          : null;
      if (fn) {
        fn(this.submitButton, isLoading, isLoading ? 'Guardando...' : undefined);
      } else {
        this.submitButton.disabled = isLoading;
      }
    }

    async parseJsonResponse(response) {
      const fn =
        this.helpers && typeof this.helpers.parseJsonResponse === 'function'
          ? this.helpers.parseJsonResponse
          : null;
      if (fn) {
        return fn(response);
      }
      let data = {};
      try {
        data = await response.json();
      } catch (error) {
        data = {};
      }
      return { ok: response.ok, status: response.status, data: data };
    }

    redirectToLogin() {
      const fn =
        this.helpers && typeof this.helpers.redirectToLogin === 'function'
          ? this.helpers.redirectToLogin
          : null;
      if (fn) {
        fn();
      } else {
        window.location.reload();
      }
    }

    sendTaskAction(action, title, extra) {
      const fn =
        this.helpers && typeof this.helpers.sendTaskAction === 'function'
          ? this.helpers.sendTaskAction
          : null;
      if (fn) {
        fn(action, title, extra);
      } else {
        console.info('Acción:', action, title, extra);
      }
    }

    triggerHaptic(type) {
      if (!this.telegram || !this.telegram.HapticFeedback || !this.telegram.HapticFeedback.notificationOccurred) {
        return;
      }
      try {
        this.telegram.HapticFeedback.notificationOccurred(type);
      } catch (error) {
        console.info('No se pudo ejecutar la vibración del dispositivo:', error);
      }
    }

    formatValueForInput(rawValue) {
      if (rawValue === null || rawValue === undefined || rawValue === '') {
        return '';
      }
      if (typeof rawValue === 'string' && !INTEGER_PATTERN.test(rawValue)) {
        return '';
      }
      const parsed = Number(rawValue);
      if (!Number.isFinite(parsed)) {
        return '';
      }
      return String(Math.trunc(parsed));
    }
  }

  function boot() {
    const card = document.querySelector('[data-production-card]');
    if (!card) {
      return;
    }
    const helpers = window.tmMiniApp || (window.tmMiniApp = {});
    const controller = new ProductionCardController(card, helpers);
    controller.init();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();

(() => {
  'use strict';

  const INTEGER_PATTERN = /^[0-9]+$/;
  const ROOM_FIELDS = ['mortality', 'discard'];
  const ROOM_FIELD_LABELS = {
    mortality: 'la mortalidad',
    discard: 'el descarte',
  };

  class NightMortalityCardController {
    constructor(card, helpers) {
      this.card = card;
      this.helpers = helpers || {};
      this.submitUrl = card.getAttribute('data-night-mortality-submit-url') || '';
      this.recordDate = card.getAttribute('data-night-mortality-date') || '';
      this.submitButton = card.querySelector('[data-night-mortality-submit-button]');
      this.feedbackNode = card.querySelector('[data-night-mortality-feedback]');
      this.successBanner = card.querySelector('[data-night-mortality-complete-banner]');
      this.csrfToken = this.helpers.csrfToken || null;
      this.saveInFlight = false;
      this.telegram = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
      this.parseJsonResponse =
        typeof this.helpers.parseJsonResponse === 'function'
          ? this.helpers.parseJsonResponse
          : async (response) => {
              const payload = await response.json().catch(() => null);
              return {
                ok: response.ok,
                status: response.status,
                data: payload,
              };
            };
      this.redirectToLogin =
        typeof this.helpers.redirectToLogin === 'function'
          ? this.helpers.redirectToLogin
          : () => window.location.reload();

      this.handleSubmit = this.handleSubmit.bind(this);
    }

    init() {
      this.lotNodes = Array.prototype.slice.call(this.card.querySelectorAll('[data-night-mortality-lot]'));
      this.bindInputs();
      if (this.submitButton) {
        this.submitButton.addEventListener('click', this.handleSubmit);
      }
    }

    bindInputs() {
      this.lotNodes.forEach((lotNode) => {
        const inputs = Array.prototype.slice.call(lotNode.querySelectorAll('[data-night-mortality-room-field]'));
        inputs.forEach((input) => {
          input.addEventListener('input', () => {
            input.classList.remove('border-rose-300');
            this.hideFeedback();
          });
        });
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

      const payload = this.collectPayload();
      if (!payload) {
        return;
      }
      this.persist(payload);
    }

    collectPayload() {
      if (!this.lotNodes.length) {
        this.showFeedback('No hay lotes activos para registrar.', 'error');
        return null;
      }
      const lots = [];
      for (let index = 0; index < this.lotNodes.length; index += 1) {
        const lotEntry = this.collectLotEntry(this.lotNodes[index]);
        if (!lotEntry) {
          return null;
        }
        lots.push(lotEntry);
      }
      return {
        date: this.recordDate,
        lots,
      };
    }

    collectLotEntry(lotNode) {
      const batchId = lotNode.getAttribute('data-batch-id');
      if (!batchId) {
        this.showFeedback('No se pudo identificar un lote para registrar.', 'error');
        return null;
      }
      const numericBatch = parseInt(batchId, 10);
      const entry = {
        bird_batch: Number.isFinite(numericBatch) ? numericBatch : batchId,
        rooms: [],
      };

      const roomNodes = Array.prototype.slice.call(lotNode.querySelectorAll('[data-night-mortality-room]'));
      if (!roomNodes.length) {
        this.showFeedback('No encontramos salones asignados a este lote.', 'error');
        return null;
      }

      for (let idx = 0; idx < roomNodes.length; idx += 1) {
        const roomNode = roomNodes[idx];
        const roomId = roomNode.getAttribute('data-room-id');
        const roomLabel = roomNode.getAttribute('data-room-label') || '';
        if (!roomId) {
          this.showFeedback('No se pudo identificar uno de los salones del lote.', 'error');
          return null;
        }
        const parsedRoomId = parseInt(roomId, 10);
        if (!Number.isFinite(parsedRoomId)) {
          this.showFeedback('Encontramos un identificador de salón inválido.', 'error');
          return null;
        }

        const fieldValues = {};
        for (let fieldIndex = 0; fieldIndex < ROOM_FIELDS.length; fieldIndex += 1) {
          const field = ROOM_FIELDS[fieldIndex];
          const result = this.readNumericField(roomNode, field, roomLabel);
          if (!result.valid) {
            return null;
          }
          fieldValues[field] = result.value;
        }
        entry.rooms.push(
          Object.assign(
            {
              room_id: parsedRoomId,
            },
            fieldValues
          )
        );
      }
      return entry;
    }

    async persist(payload) {
      if (!this.submitButton) {
        return;
      }
      this.saveInFlight = true;
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
        if (data.night_mortality) {
          this.updateFromResponse(data.night_mortality);
          this.markComplete();
        }
        this.showFeedback('Mortalidad actualizada correctamente.', 'success');
        this.triggerHaptic('success');
      } catch (error) {
        console.warn('No se pudo guardar la mortalidad nocturna:', error);
        this.showFeedback('No fue posible guardar los registros. Intenta de nuevo.', 'error');
        this.triggerHaptic('error');
      } finally {
        this.saveInFlight = false;
        this.setButtonLoading(false);
      }
    }

    updateFromResponse(payload) {
      if (!payload) {
        return;
      }
      if (payload.date) {
        this.recordDate = payload.date;
        this.card.setAttribute('data-night-mortality-date', payload.date);
      }
      if (!Array.isArray(payload.lots)) {
        return;
      }

      this.lotNodes = Array.prototype.slice.call(this.card.querySelectorAll('[data-night-mortality-lot]'));
      const lotMap = new Map();
      this.lotNodes.forEach((lotNode) => {
        const batchId = lotNode.getAttribute('data-batch-id');
        if (batchId) {
          lotMap.set(String(batchId), lotNode);
        }
      });

      payload.lots.forEach((lotPayload) => {
        const target = lotMap.get(String(lotPayload.id));
        if (!target || !Array.isArray(lotPayload.rooms)) {
          return;
        }
        const roomMap = new Map();
        const roomNodes = Array.prototype.slice.call(target.querySelectorAll('[data-night-mortality-room]'));
        roomNodes.forEach((roomNode) => {
          const roomId = roomNode.getAttribute('data-room-id');
          if (roomId) {
            roomMap.set(String(roomId), roomNode);
          }
        });
        lotPayload.rooms.forEach((roomPayload) => {
          const roomNode = roomMap.get(String(roomPayload.id));
          if (!roomNode) {
            return;
          }
          ROOM_FIELDS.forEach((field) => {
            const input = roomNode.querySelector('[data-night-mortality-room-field="' + field + '"]');
            if (!input) {
              return;
            }
            const value = roomPayload[field];
            input.value = value === null || value === undefined ? '' : String(value);
          });
        });
      });
    }

    markComplete() {
      if (this.successBanner) {
        this.successBanner.classList.remove('hidden');
      }
    }

    setButtonLoading(isLoading) {
      if (!this.submitButton) {
        return;
      }
      this.submitButton.disabled = isLoading;
      const labelNode = this.submitButton.querySelector('[data-night-mortality-button-label]');
      const iconNode = this.submitButton.querySelector('[data-night-mortality-button-icon]');
      if (isLoading) {
        if (labelNode) {
          labelNode.textContent = 'Guardando...';
        }
        if (iconNode) {
          iconNode.textContent = '⏳';
        }
      } else {
        if (labelNode) {
          labelNode.textContent = 'Guardar mortalidad';
        }
        if (iconNode) {
          iconNode.textContent = '💾';
        }
      }
    }

    showFeedback(message, tone) {
      if (!this.feedbackNode) {
        return;
      }
      const toneClass = tone === 'success' ? 'text-emerald-600' : 'text-rose-600';
      this.feedbackNode.textContent = message;
      this.feedbackNode.classList.remove('hidden', 'text-rose-600', 'text-emerald-600');
      this.feedbackNode.classList.add(toneClass);
    }

    hideFeedback() {
      if (!this.feedbackNode) {
        return;
      }
      this.feedbackNode.textContent = '';
      this.feedbackNode.classList.add('hidden');
    }

    triggerHaptic(type) {
      if (!this.telegram || !this.telegram.HapticFeedback || !this.telegram.HapticFeedback.notificationOccurred) {
        return;
      }
      try {
        this.telegram.HapticFeedback.notificationOccurred(type);
      } catch (error) {
        console.warn('No se pudo ejecutar la vibración del dispositivo:', error);
      }
    }
  }

  function boot() {
    const cards = document.querySelectorAll('[data-night-mortality-card]');
    if (!cards.length) {
      return;
    }
    const helpers = window.tmMiniApp || (window.tmMiniApp = {});
    cards.forEach((card) => {
      const controller = new NightMortalityCardController(card, helpers);
      controller.init();
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
    readNumericField(roomNode, field, roomLabel) {
      const input = roomNode.querySelector('[data-night-mortality-room-field="' + field + '"]');
      const value = input && typeof input.value === 'string' ? input.value.trim() : '';
      if (value && !INTEGER_PATTERN.test(value)) {
        const label = ROOM_FIELD_LABELS[field] || 'este campo';
        this.showFeedback(
          roomLabel ? `Usa números enteros para ${label} de ${roomLabel}.` : 'Usa números enteros.',
          'error'
        );
        if (input) {
          input.classList.add('border-rose-300');
          if (typeof input.focus === 'function') {
            input.focus();
          }
        }
        return { valid: false, value: '' };
      }
      return { valid: true, value };
    }

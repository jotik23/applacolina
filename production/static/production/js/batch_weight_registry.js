(function () {
  'use strict';

  const modal = document.querySelector('[data-weight-modal]');
  if (!modal) {
    return;
  }
  const card = modal.querySelector('[data-weight-card]');
  if (!card) {
    return;
  }

  const triggers = document.querySelectorAll('[data-weight-trigger]');
  const overlay = modal.querySelector('[data-weight-modal-overlay]');
  const closeButton = modal.querySelector('[data-weight-modal-close]');
  const locationList = card.querySelector('[data-weight-location-list]');
  const idleActions = card.querySelector('[data-weight-idle-actions]');
  const panel = card.querySelector('[data-weight-panel]');
  const startButton = card.querySelector('[data-weight-start]');
  const resumeButton = card.querySelector('[data-weight-resume]');
  const selectionHint = card.querySelector('[data-weight-selection-hint]');
  const activeSalonsLabel = card.querySelector('[data-weight-active-salons]');
  const totalCountLabel = card.querySelector('[data-weight-total-count]');
  const input = card.querySelector('[data-weight-input]');
  const addButton = card.querySelector('[data-weight-add]');
  const copyButton = card.querySelector('[data-weight-copy]');
  const resetButton = card.querySelector('[data-weight-reset]');
  const pauseButton = card.querySelector('[data-weight-pause]');
  const finishButton = card.querySelector('[data-weight-finish]');
  const statusNode = card.querySelector('[data-weight-status]');
  const emptyState = card.querySelector('[data-weight-empty-state]');
  const entriesList = card.querySelector('[data-weight-list]');
  const summaryNode = card.querySelector('[data-weight-summary]');
  const countNode = card.querySelector('[data-weight-count]');
  const averageNode = card.querySelector('[data-weight-average]');
  const uniformityNode = card.querySelector('[data-weight-uniformity]');
  const varianceNode = card.querySelector('[data-weight-variance]');
  const rangeNode = card.querySelector('[data-weight-range]');
  const warningNode = card.querySelector('[data-weight-sample-warning]');

  const tolerancePercent = normalizeNumber(card.getAttribute('data-weight-tolerance')) || 10;
  const minSampleSize = normalizeNumber(card.getAttribute('data-weight-minimum')) || 30;
  const submitUrl = card.getAttribute('data-weight-submit-url') || '';
  const weightDate = card.getAttribute('data-weight-date') || '';
  const csrfToken = modal.getAttribute('data-csrf-token') || getCookie('csrftoken') || '';

  const locationButtons = locationList ? Array.prototype.slice.call(locationList.querySelectorAll('[data-weight-location]')) : [];
  const locations = locationButtons.map(function (button) {
    return {
      id: button.getAttribute('data-weight-location'),
      button: button,
      label: button.getAttribute('data-weight-location-label') || button.textContent.trim(),
      room: button.getAttribute('data-weight-location-room') || '',
      barn: button.getAttribute('data-weight-location-barn') || '',
      farm: button.getAttribute('data-weight-location-farm') || '',
      birds: normalizeNumber(button.getAttribute('data-weight-location-birds')),
      nodes: {
        progress: button.querySelector('[data-weight-location-progress]'),
        check: button.querySelector('[data-weight-location-check]'),
        meta: button.querySelector('[data-weight-location-meta]'),
      },
    };
  });
  const locationsById = {};
  locations.forEach(function (location) {
    if (location.id) {
      locationsById[location.id] = location;
    }
  });

  const state = {
    activeId: null,
    sessions: {},
    order: locations.map(function (location) {
      return location.id;
    }),
    dirty: false,
    panelOpen: false,
    submitting: false,
  };

  loadInitialSessions();
  updateLocationButtons();
  updateTotals();
  updateSelectionHint();

  triggers.forEach(function (trigger) {
    trigger.addEventListener('click', function (event) {
      event.preventDefault();
      openModal();
    });
  });

  if (overlay) {
    overlay.addEventListener('click', closeModal);
  }
  if (closeButton) {
    closeButton.addEventListener('click', closeModal);
  }

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && !modal.classList.contains('hidden')) {
      closeModal();
    }
  });

  locationButtons.forEach(function (button) {
    button.addEventListener('click', function () {
      selectLocation(button.getAttribute('data-weight-location'), { focusInput: true });
    });
  });

  if (startButton) {
    startButton.addEventListener('click', function () {
      const first = state.order[0];
      selectLocation(first, { focusInput: true });
    });
  }

  if (resumeButton) {
    resumeButton.addEventListener('click', function () {
      const target = state.order.find(function (id) {
        const session = state.sessions[id];
        return session && session.entries.length > 0;
      });
      if (target) {
        selectLocation(target, { focusInput: true });
      }
    });
  }

  function submitWeightFromInput() {
    const grams = parseWeightInput(input ? input.value : '');
    if (!Number.isFinite(grams) || grams <= 0) {
      setStatus('Ingresa un peso válido', 'warning');
      return false;
    }
    const added = addEntry(grams);
    if (added && input) {
      input.value = '';
      input.focus();
    }
    return added;
  }

  if (addButton) {
    addButton.addEventListener('click', function () {
      submitWeightFromInput();
    });
  }

  if (input) {
    input.addEventListener('keydown', function (event) {
      if (event.key !== 'Enter') {
        return;
      }
      event.preventDefault();
      submitWeightFromInput();
    });
  }

  if (copyButton) {
    copyButton.addEventListener('click', function () {
      copyActiveEntries();
    });
  }

  if (resetButton) {
    resetButton.addEventListener('click', function () {
      const session = getActiveSession();
      if (!session || !session.entries.length) {
        return;
      }
      const confirmed = window.confirm('¿Deseas eliminar todos los pesos de este salón?');
      if (!confirmed) {
        return;
      }
      session.entries = [];
      session.status = 'idle';
      state.dirty = true;
      renderEntries();
      updateLocationButtons();
      updateTotals();
      setStatus('Pesos reiniciados.', 'warning');
    });
  }

  if (pauseButton) {
    pauseButton.addEventListener('click', function () {
      closePanel();
      setStatus('Registro pausado', 'info');
    });
  }

  if (finishButton) {
    finishButton.addEventListener('click', function () {
      submitSessions();
    });
  }

  function openModal() {
    modal.classList.remove('hidden');
    document.documentElement.classList.add('overflow-hidden');
    document.body.classList.add('overflow-hidden');
  }

  function closeModal() {
    modal.classList.add('hidden');
    document.documentElement.classList.remove('overflow-hidden');
    document.body.classList.remove('overflow-hidden');
    closePanel();
  }

  function openPanel(options) {
    state.panelOpen = true;
    if (panel) {
      panel.classList.remove('hidden');
    }
    if (idleActions) {
      idleActions.classList.add('hidden');
    }
    if (options && options.focusInput && input) {
      setTimeout(function () {
        input.focus();
      }, 30);
    }
    updateResumeVisibility();
  }

  function closePanel() {
    state.panelOpen = false;
    if (panel) {
      panel.classList.add('hidden');
    }
    if (idleActions) {
      idleActions.classList.remove('hidden');
    }
    updateResumeVisibility();
    state.activeId = null;
    highlightActiveButton();
    renderEntries();
    updateSelectionHint();
  }

  function loadInitialSessions() {
    const node = document.getElementById('batch-weight-sessions');
    let payload = [];
    if (node) {
      try {
        payload = JSON.parse(node.textContent || '[]');
      } catch (error) {
        console.warn('No se pudo leer el registro de pesajes inicial.', error);
      }
    }
    if (!Array.isArray(payload)) {
      payload = [];
    }
    payload.forEach(function (sessionData) {
      const id = sessionData && sessionData.id ? sessionData.id : null;
      if (!id || !locationsById[id]) {
        return;
      }
      const entries = Array.isArray(sessionData.entries) ? sessionData.entries : [];
      const normalizedEntries = entries
        .map(function (value) {
          const grams = Number(value);
          return Number.isFinite(grams) && grams > 0 ? grams : null;
        })
        .filter(function (value) {
          return value !== null;
        })
        .map(function (grams) {
          return { id: createEntryId(), grams: round(grams, 2) };
        });
      state.sessions[id] = buildSessionFromLocation(locationsById[id], normalizedEntries);
    });
  }

  function buildSessionFromLocation(location, entries) {
    return {
      id: location.id,
      label: location.label,
      room: location.room,
      barn: location.barn,
      farm: location.farm,
      birds: location.birds,
      entries: entries || [],
      status: entries && entries.length ? 'saved' : 'idle',
    };
  }

  function ensureSession(id) {
    if (!id || !locationsById[id]) {
      return null;
    }
    if (!state.sessions[id]) {
      state.sessions[id] = buildSessionFromLocation(locationsById[id], []);
    }
    return state.sessions[id];
  }

  function selectLocation(id, options) {
    if (!id) {
      return;
    }
    const session = ensureSession(id);
    if (!session) {
      return;
    }
    state.activeId = id;
    highlightActiveButton();
    renderEntries();
    updateTotals();
    updateSelectionHint();
    openPanel(options || {});
    const label = session.room || session.label;
    if (label) {
      setStatus(label + ' listo para captura.', 'info');
    }
  }

  function highlightActiveButton() {
    locationButtons.forEach(function (button) {
      const id = button.getAttribute('data-weight-location');
      if (id === state.activeId) {
        button.classList.add('ring-2', 'ring-sky-400');
      } else {
        button.classList.remove('ring-2', 'ring-sky-400');
      }
    });
  }

  function renderEntries() {
    const session = getActiveSession();
    if (!session || !session.entries.length) {
      if (emptyState) {
        emptyState.classList.remove('hidden');
      }
      if (entriesList) {
        entriesList.classList.add('hidden');
        entriesList.innerHTML = '';
      }
      if (summaryNode) {
        summaryNode.classList.add('hidden');
      }
      return;
    }
    if (emptyState) {
      emptyState.classList.add('hidden');
    }
    if (entriesList) {
      entriesList.classList.remove('hidden');
      entriesList.innerHTML = '';
      session.entries.forEach(function (entry) {
        const chip = document.createElement('button');
        chip.type = 'button';
        chip.className =
          'inline-flex items-center gap-1 rounded-full bg-sky-100 px-3 py-1 text-[11px] font-semibold text-sky-700 transition hover:bg-sky-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-200';
        chip.setAttribute('data-entry-id', entry.id);
        chip.textContent = formatKg(entry.grams / 1000);
        const remove = document.createElement('span');
        remove.className = 'text-sky-500';
        remove.textContent = '✕';
        chip.appendChild(remove);
        chip.addEventListener('click', function () {
          removeEntry(entry.id);
        });
        entriesList.appendChild(chip);
      });
    }
    updateSummary(session);
  }

  function getActiveSession() {
    if (!state.activeId) {
      return null;
    }
    return state.sessions[state.activeId] || null;
  }

  function addEntry(grams) {
    const session = getActiveSession();
    if (!session) {
      setStatus('Selecciona un salón antes de registrar pesos.', 'warning');
      return false;
    }
    session.entries.push({
      id: createEntryId(),
      grams: round(grams, 2),
    });
    session.status = 'in-progress';
    state.dirty = true;
    renderEntries();
    updateLocationButtons();
    updateTotals();
    setStatus('Peso agregado.', 'success');
    return true;
  }

  function removeEntry(entryId) {
    const session = getActiveSession();
    if (!session) {
      return;
    }
    const index = session.entries.findIndex(function (entry) {
      return entry.id === entryId;
    });
    if (index === -1) {
      return;
    }
    session.entries.splice(index, 1);
    if (!session.entries.length) {
      session.status = 'idle';
    }
    state.dirty = true;
    renderEntries();
    updateLocationButtons();
    updateTotals();
    setStatus('Peso eliminado.', 'warning');
  }

  function updateSummary(session) {
    if (!summaryNode || !session) {
      return;
    }
    const metrics = computeMetrics(
      session.entries.map(function (entry) {
        return entry.grams;
      }),
      tolerancePercent
    );
    if (!metrics || !metrics.count) {
      summaryNode.classList.add('hidden');
      return;
    }
    summaryNode.classList.remove('hidden');
    if (countNode) {
      countNode.textContent = formatCount(metrics.count);
    }
    if (averageNode) {
      averageNode.textContent = formatKg(metrics.average_grams / 1000);
    }
    if (uniformityNode) {
      uniformityNode.textContent = formatPercentage(metrics.uniformity_percent);
    }
    if (varianceNode) {
      varianceNode.textContent = formatVariance(metrics.variance_grams);
    }
    if (rangeNode) {
      rangeNode.textContent = formatRange(metrics.min_grams, metrics.max_grams);
    }
    if (warningNode) {
      if (metrics.count < minSampleSize) {
        warningNode.classList.remove('hidden');
      } else {
        warningNode.classList.add('hidden');
      }
    }
  }

  function updateLocationButtons() {
    locations.forEach(function (location) {
      const session = ensureSession(location.id);
      if (!session) {
        return;
      }
      const hasEntries = session.entries.length > 0;
      if (location.nodes.progress) {
        location.nodes.progress.textContent = hasEntries ? formatCount(session.entries.length) : '—';
      }
      if (location.nodes.check) {
        if (hasEntries) {
          location.nodes.check.classList.remove('hidden');
        } else {
          location.nodes.check.classList.add('hidden');
        }
      }
      if (location.button) {
        if (hasEntries) {
          location.button.classList.add('border-emerald-200', 'text-emerald-700');
        } else {
          location.button.classList.remove('border-emerald-200', 'text-emerald-700');
        }
      }
      if (location.nodes.meta) {
        if (location.birds) {
          location.nodes.meta.textContent = formatCount(location.birds);
        } else {
          location.nodes.meta.textContent = '—';
        }
      }
    });
    updateResumeVisibility();
  }

  function updateTotals() {
    const totalEntries = state.order.reduce(function (acc, id) {
      const session = state.sessions[id];
      if (!session || !session.entries) {
        return acc;
      }
      return acc + session.entries.length;
    }, 0);
    if (totalCountLabel) {
      totalCountLabel.textContent = formatCount(totalEntries);
    }
    const session = getActiveSession();
    if (!activeSalonsLabel) {
      return;
    }
    if (!session) {
      activeSalonsLabel.textContent = '—';
      return;
    }
    const parts = [];
    if (session.barn) {
      parts.push(session.barn);
    }
    if (session.room) {
      parts.push(session.room);
    } else if (session.label) {
      parts.push(session.label);
    }
    activeSalonsLabel.textContent = parts.length ? parts.join(' · ') : session.label || '—';
  }

  function updateResumeVisibility() {
    if (!resumeButton) {
      return;
    }
    const hasSessions = state.order.some(function (id) {
      const session = state.sessions[id];
      return session && session.entries.length > 0;
    });
    if (!state.panelOpen && hasSessions) {
      resumeButton.classList.remove('hidden');
    } else {
      resumeButton.classList.add('hidden');
    }
  }

  function updateSelectionHint() {
    if (!selectionHint) {
      return;
    }
    const hasActive = Boolean(state.activeId);
    if (!hasActive) {
      selectionHint.textContent = 'Selecciona al menos un salón para comenzar.';
      return;
    }
    const session = state.sessions[state.activeId];
    if (!session) {
      selectionHint.textContent = 'Selecciona al menos un salón para comenzar.';
      return;
    }
    if (!session.entries.length) {
      selectionHint.textContent = 'Listo para iniciar muestreo en ' + (session.room || session.label || 'este salón') + '.';
      return;
    }
    selectionHint.textContent = 'Registro en curso. Continúa capturando o envía los datos.';
  }

  function copyActiveEntries() {
    const session = getActiveSession();
    if (!session || !session.entries.length) {
      setStatus('No hay pesos para copiar.', 'warning');
      return;
    }
    const rows = session.entries
      .slice()
      .map(function (entry) {
        return Math.round(entry.grams).toString();
      })
      .join('\n');
    navigator.clipboard
      .writeText(rows)
      .then(function () {
        setStatus('Pesos copiados al portapapeles.', 'success');
      })
      .catch(function () {
        setStatus('No pudimos copiar los pesos.', 'error');
      });
  }

  function submitSessions() {
    if (!submitUrl) {
      setStatus('No hay un destino configurado para guardar.', 'error');
      return;
    }
    const payloadSessions = state.order
      .map(function (id) {
        const session = state.sessions[id];
        if (!session) {
          return null;
        }
        return {
          id: session.id,
          entries: session.entries.map(function (entry) {
            return entry.grams;
          }),
        };
      })
      .filter(function (item) {
        return !!item;
      });
    const hasValues = payloadSessions.some(function (session) {
      return session.entries && session.entries.length;
    });
    if (!hasValues) {
      setStatus('Agrega pesos antes de enviar.', 'warning');
      return;
    }
    if (state.submitting) {
      return;
    }
    state.submitting = true;
    setButtonLoading(finishButton, true, 'Guardando…');
    fetch(submitUrl, {
      method: 'POST',
      credentials: 'include',
      headers: Object.assign(
        { 'Content-Type': 'application/json' },
        csrfToken ? { 'X-CSRFToken': csrfToken } : {}
      ),
      body: JSON.stringify({
        date: weightDate,
        sessions: payloadSessions,
      }),
    })
      .then(function (response) {
        if (!response.ok) {
          return response.json().then(function (data) {
            throw new Error((data && data.error) || 'No pudimos guardar el registro.');
          });
        }
        return response.json();
      })
      .then(function (data) {
        const refreshed = data && data.weight_registry ? data.weight_registry.sessions : null;
        if (Array.isArray(refreshed)) {
          refreshed.forEach(function (sessionData) {
            const id = sessionData && sessionData.id ? sessionData.id : null;
            if (!id || !locationsById[id]) {
              return;
            }
            const entries = Array.isArray(sessionData.entries) ? sessionData.entries : [];
            state.sessions[id] = buildSessionFromLocation(
              locationsById[id],
              entries
                .map(function (value) {
                  const grams = Number(value);
                  return Number.isFinite(grams) && grams > 0 ? { id: createEntryId(), grams: round(grams, 2) } : null;
                })
                .filter(function (entry) {
                  return entry !== null;
                })
            );
          });
        }
        state.dirty = false;
        setStatus('Registro enviado correctamente.', 'success');
        renderEntries();
        updateLocationButtons();
        updateTotals();
      })
      .catch(function (error) {
        setStatus(error.message || 'No pudimos guardar el registro.', 'error');
      })
      .finally(function () {
        state.submitting = false;
        setButtonLoading(finishButton, false);
      });
  }

  function setButtonLoading(button, isLoading, label) {
    if (!button) {
      return;
    }
    if (isLoading) {
      button.dataset.originalLabel = button.textContent;
      button.disabled = true;
      button.textContent = label || 'Guardando…';
    } else {
      button.disabled = false;
      const original = button.dataset.originalLabel;
      if (original) {
        button.textContent = original;
        delete button.dataset.originalLabel;
      }
    }
  }

  function normalizeNumber(value) {
    if (value === null || value === undefined || value === '') {
      return null;
    }
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric : null;
  }

  function round(value, decimals) {
    if (!Number.isFinite(value)) {
      return 0;
    }
    const factor = Math.pow(10, decimals || 0);
    return Math.round(value * factor) / factor;
  }

  function parseWeightInput(rawValue) {
    if (rawValue === null || rawValue === undefined) {
      return null;
    }
    const normalized = String(rawValue).trim().replace(',', '.');
    if (!normalized) {
      return null;
    }
    const numeric = Number(normalized);
    if (!Number.isFinite(numeric) || numeric <= 0) {
      return null;
    }
    if (numeric < 20) {
      return numeric * 1000;
    }
    return numeric;
  }

  function computeMetrics(entries, tolerance) {
    if (!entries.length) {
      return null;
    }
    const count = entries.length;
    const sum = entries.reduce(function (acc, value) {
      return acc + value;
    }, 0);
    const average = sum / count;
    let variance = 0;
    const toleranceValue = (average * tolerance) / 100;
    const lower = average - toleranceValue;
    const upper = average + toleranceValue;
    let within = 0;
    let min = entries[0];
    let max = entries[0];
    entries.forEach(function (value) {
      variance += Math.pow(value - average, 2);
      if (value >= lower && value <= upper) {
        within += 1;
      }
      if (value < min) {
        min = value;
      }
      if (value > max) {
        max = value;
      }
    });
    variance = variance / count;
    const uniformity = (within / count) * 100;
    return {
      count: count,
      average_grams: average,
      variance_grams: variance,
      min_grams: min,
      max_grams: max,
      uniformity_percent: uniformity,
    };
  }

  function formatCount(value) {
    if (!Number.isFinite(value)) {
      return '0 aves';
    }
    const rounded = Math.round(value);
    return rounded === 1 ? '1 ave' : rounded + ' aves';
  }

  function formatKg(value) {
    if (!Number.isFinite(value)) {
      return '—';
    }
    return value.toFixed(3).replace(/\.000$/, '') + ' kg';
  }

  function formatPercentage(value) {
    if (!Number.isFinite(value)) {
      return '—';
    }
    return value.toFixed(1).replace(/\.0$/, '') + ' %';
  }

  function formatVariance(value) {
    if (!Number.isFinite(value)) {
      return '—';
    }
    const varianceKg = value / 1000000;
    return varianceKg.toFixed(3).replace(/\.000$/, '') + ' kg²';
  }

  function formatRange(min, max) {
    if (!Number.isFinite(min) || !Number.isFinite(max)) {
      return '—';
    }
    return formatKg(min / 1000) + ' – ' + formatKg(max / 1000);
  }

  function setStatus(message, tone) {
    if (!statusNode) {
      return;
    }
    statusNode.textContent = message;
    statusNode.classList.remove('text-slate-400', 'text-emerald-600', 'text-amber-600', 'text-rose-600');
    if (tone === 'success' || tone === 'info') {
      statusNode.classList.add('text-emerald-600');
    } else if (tone === 'warning') {
      statusNode.classList.add('text-amber-600');
    } else if (tone === 'error') {
      statusNode.classList.add('text-rose-600');
    } else {
      statusNode.classList.add('text-slate-400');
    }
  }

  function createEntryId() {
    return 'w-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
  }

  function getCookie(name) {
    if (typeof document === 'undefined' || !document.cookie) {
      return '';
    }
    const value = '; ' + document.cookie;
    const parts = value.split('; ' + name + '=');
    if (parts.length === 2) {
      return parts.pop().split(';').shift();
    }
    return '';
  }
})();

(function () {
  'use strict';

  function onReady(callback) {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', callback, { once: true });
      return;
    }
    callback();
  }

  function copyText(text) {
    if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
      return navigator.clipboard.writeText(text);
    }
    return fallbackCopy(text);
  }

  function fallbackCopy(text) {
    return new Promise(function (resolve, reject) {
      var textarea = document.createElement('textarea');
      textarea.value = text;
      textarea.setAttribute('readonly', '');
      textarea.style.position = 'absolute';
      textarea.style.left = '-9999px';
      document.body.appendChild(textarea);
      textarea.select();
      try {
        var successful = document.execCommand('copy');
        document.body.removeChild(textarea);
        if (successful) {
          resolve();
          return;
        }
        reject(new Error('No se pudo copiar al portapapeles.'));
      } catch (error) {
        document.body.removeChild(textarea);
        reject(error);
      }
    });
  }

  function readClipboardText() {
    if (navigator.clipboard && typeof navigator.clipboard.readText === 'function') {
      return navigator.clipboard.readText();
    }
    return new Promise(function (resolve, reject) {
      var manual = window.prompt('Pega aquí los datos copiados de la tabla genética:');
      if (!manual) {
        reject(new Error('No se recibió texto para pegar.'));
        return;
      }
      resolve(manual);
    });
  }

  function dispatchInputEvent(target) {
    if (!target || typeof target.dispatchEvent !== 'function') {
      return;
    }
    try {
      var modernEvent = new Event('input', { bubbles: true });
      target.dispatchEvent(modernEvent);
    } catch (_error) {
      var legacyEvent = document.createEvent('Event');
      legacyEvent.initEvent('input', true, true);
      target.dispatchEvent(legacyEvent);
    }
  }

  onReady(function () {
    var form = document.querySelector('[data-reference-table]');
    if (!form) {
      return;
    }

    var copyButton = form.querySelector('[data-reference-copy]');
    var pasteButton = form.querySelector('[data-reference-paste]');
    var feedback = form.querySelector('[data-reference-feedback]');
    var breedName = form.getAttribute('data-reference-breed-name') || 'raza';
    var breedId = form.getAttribute('data-reference-breed-id');

    function updateFeedback(message, variant) {
      if (!feedback) {
        if (variant === 'error') {
          console.error(message);
        } else {
          console.log(message);
        }
        return;
      }
      var classes = ['text-emerald-600', 'text-slate-600', 'text-amber-600', 'text-rose-600'];
      feedback.classList.remove('hidden');
      classes.forEach(function (className) {
        feedback.classList.remove(className);
      });
      var classMap = {
        success: 'text-emerald-600',
        info: 'text-slate-600',
        warning: 'text-amber-600',
        error: 'text-rose-600',
      };
      feedback.classList.add(classMap[variant] || classMap.info);
      feedback.textContent = message;
    }

    function gatherPayload() {
      var payload = {
        version: 1,
        exportedAt: new Date().toISOString(),
        breed: {
          id: breedId || null,
          name: breedName,
        },
        fields: {},
      };
      var inputs = form.querySelectorAll('[data-reference-field]');
      Array.prototype.forEach.call(inputs, function (input) {
        var value = input.value;
        if (value === undefined || value === null || value === '') {
          return;
        }
        payload.fields[input.name] = value;
      });
      return payload;
    }

    function parsePayload(rawText) {
      var parsed = JSON.parse(rawText);
      if (!parsed || typeof parsed !== 'object') {
        throw new Error('El contenido copiado no es válido.');
      }
      if (parsed.version !== 1 || typeof parsed.fields !== 'object') {
        throw new Error('Los datos pegados no corresponden a una tabla genética.');
      }
      return parsed;
    }

    function applyPayload(payload) {
      var fields = payload.fields || {};
      var keys = Object.keys(fields);
      var applied = 0;
      keys.forEach(function (name) {
        var element = form.elements.namedItem(name);
        if (!element) {
          return;
        }
        var target = element;
        if (
          typeof window.RadioNodeList !== 'undefined' &&
          element instanceof window.RadioNodeList
        ) {
          target = element.length ? element[0] : null;
        }
        if (!target || typeof target.value === 'undefined') {
          return;
        }
        target.value = fields[name];
        dispatchInputEvent(target);
        applied += 1;
      });
      return {
        applied: applied,
        total: keys.length,
      };
    }

    function handleCopy() {
      var payload = gatherPayload();
      var fieldCount = Object.keys(payload.fields).length;
      if (fieldCount === 0) {
        updateFeedback('No hay valores capturados en esta raza para copiar.', 'warning');
        return;
      }
      var serialized = JSON.stringify(payload, null, 2);
      copyText(serialized)
        .then(function () {
          updateFeedback('Se copiaron ' + fieldCount + ' campos de la tabla de ' + breedName + '.', 'success');
        })
        .catch(function (error) {
          updateFeedback(error.message || 'No se pudo copiar al portapapeles.', 'error');
        });
    }

    function handlePaste() {
      readClipboardText()
        .then(function (text) {
          var payload = parsePayload(text);
          var result = applyPayload(payload);
          if (result.applied === 0) {
            updateFeedback('No se encontraron campos coincidentes para pegar.', 'warning');
            return;
          }
          updateFeedback('Se pegaron ' + result.applied + ' campos en esta tabla.', 'success');
        })
        .catch(function (error) {
          updateFeedback(error.message || 'No fue posible leer el portapapeles.', 'error');
        });
    }

    if (copyButton) {
      copyButton.addEventListener('click', handleCopy);
    }
    if (pasteButton) {
      pasteButton.addEventListener('click', handlePaste);
    }
  });
})();

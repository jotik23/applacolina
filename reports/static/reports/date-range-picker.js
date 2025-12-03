(function () {
  function ready(fn) {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', fn);
    } else {
      fn();
    }
  }

  function formatDisplay(start, end) {
    if (!start && !end) {
      return '';
    }
    if (start && !end) {
      return start;
    }
    if (!start && end) {
      return end;
    }
    return start + ' → ' + end;
  }

  ready(function () {
    var inputs = document.querySelectorAll('[data-date-range]');
    if (!inputs.length) {
      return;
    }

    if (typeof Litepicker === 'undefined') {
      console.warn('Litepicker no disponible para los rangos de fecha.');
      return;
    }

    inputs.forEach(function (input) {
      var startSelector = input.getAttribute('data-range-start');
      var endSelector = input.getAttribute('data-range-end');
      var startField = startSelector ? document.querySelector(startSelector) : null;
      var endField = endSelector ? document.querySelector(endSelector) : null;
      var valueFormat = input.getAttribute('data-range-format') || 'YYYY-MM-DD';
      var displayFormat = input.getAttribute('data-range-display-format') || 'YYYY-MM-DD';
      var placeholder = input.getAttribute('data-range-placeholder') || 'Selecciona rango';

      input.setAttribute('placeholder', placeholder);
      input.setAttribute('readonly', 'readonly');

      function updateDisplay(start, end) {
        var startLabel = start ? start.format(displayFormat) : '';
        var endLabel = end ? end.format(displayFormat) : '';
        input.value = formatDisplay(startLabel, endLabel);
      }

      var picker = new Litepicker({
        element: input,
        singleMode: false,
        format: valueFormat,
        lang: 'es',
        numberOfMonths: 2,
        numberOfColumns: 2,
        autoApply: true,
        allowRepick: true,
        dropdowns: {
          minYear: 2020,
          maxYear: new Date().getFullYear() + 1,
          months: true,
          years: true,
        },
        setup: function (pickerInstance) {
          var startValue = startField ? startField.value : '';
          var endValue = endField ? endField.value : '';
          if (startValue) {
            pickerInstance.setDateRange(startValue, endValue || startValue, { format: valueFormat });
            updateDisplay(pickerInstance.getStartDate(), pickerInstance.getEndDate());
          } else {
            input.value = '';
          }
        },
      });

      picker.on('selected', function (startDate, endDate) {
        if (startField) {
          startField.value = startDate ? startDate.format(valueFormat) : '';
        }
        if (endField) {
          endField.value = endDate ? endDate.format(valueFormat) : '';
        }
        updateDisplay(startDate, endDate);
      });

    });
  });
})();

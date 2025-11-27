(function () {
  function $(selector, root) {
    return (root || document).querySelector(selector);
  }

  function createSuggestionItem(customer) {
    var wrapper = document.createElement('button');
    wrapper.type = 'button';
    wrapper.className =
      'flex w-full items-center justify-between px-3 py-2 text-left text-[12px] hover:bg-emerald-50 focus:bg-emerald-50';
    var name = document.createElement('span');
    name.className = 'font-semibold text-slate-700';
    name.textContent = customer.name || '';
    var meta = document.createElement('span');
    meta.className = 'text-[11px] text-slate-500';
    meta.textContent = customer.tax_id || '';
    wrapper.appendChild(name);
    wrapper.appendChild(meta);
    return wrapper;
  }

  function initAutocomplete(container, suggestions) {
    if (!container) {
      return;
    }
    var input = $('[data-customer-autocomplete-input]', container);
    var results = $('[data-customer-autocomplete-results]', container);
    if (!input || !results) {
      return;
    }

    function closeResults() {
      results.classList.add('hidden');
      results.innerHTML = '';
    }

    function renderSuggestions(list) {
      results.innerHTML = '';
      if (!list.length) {
        closeResults();
        return;
      }
      list.forEach(function (customer) {
        var option = createSuggestionItem(customer);
        option.addEventListener('mousedown', function (event) {
          event.preventDefault();
          input.value = customer.name || '';
          closeResults();
        });
        results.appendChild(option);
      });
      results.classList.remove('hidden');
    }

    function filterSuggestions(query) {
      var normalized = (query || '').toLowerCase();
      if (!normalized) {
        renderSuggestions(suggestions.slice(0, 5));
        return;
      }
      var filtered = suggestions.filter(function (customer) {
        var name = (customer.name || '').toLowerCase();
        var taxId = (customer.tax_id || '').toLowerCase();
        return name.includes(normalized) || taxId.includes(normalized);
      });
      renderSuggestions(filtered.slice(0, 6));
    }

    input.addEventListener('input', function () {
      filterSuggestions(input.value);
    });

    input.addEventListener('focus', function () {
      filterSuggestions(input.value);
    });

    document.addEventListener('click', function (event) {
      if (!container.contains(event.target)) {
        closeResults();
      }
    });
  }

  function initFilterDropdowns() {
    var dropdowns = document.querySelectorAll('details[data-filter-dropdown]');
    if (!dropdowns.length) {
      return;
    }
    document.addEventListener('click', function (event) {
      dropdowns.forEach(function (dropdown) {
        if (dropdown.hasAttribute('open') && !dropdown.contains(event.target)) {
          dropdown.removeAttribute('open');
        }
      });
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    var scriptTag = document.getElementById('customer-suggestions-data');
    var suggestions = [];
    try {
      if (scriptTag) {
        suggestions = JSON.parse(scriptTag.textContent || '[]');
      }
    } catch (error) {
      suggestions = [];
    }
    var container = document.querySelector('[data-customer-autocomplete-container]');
    initAutocomplete(container, suggestions || []);
    initFilterDropdowns();
  });
})();

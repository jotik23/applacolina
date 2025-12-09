(function () {
  function normalize(text) {
    return (text || "").toString().toLowerCase();
  }

  function initDropdownClosing(dropdowns) {
    if (!dropdowns.length) {
      return;
    }
    document.addEventListener("click", function (event) {
      dropdowns.forEach(function (dropdown) {
        if (dropdown.hasAttribute("open") && !dropdown.contains(event.target)) {
          dropdown.removeAttribute("open");
        }
      });
    });
  }

  function initSearchFilters(dropdowns) {
    dropdowns.forEach(function (dropdown) {
      var searchField = dropdown.querySelector("[data-filter-search]");
      if (!searchField) {
        return;
      }
      var optionsContainer = dropdown.querySelector("[data-filter-options]");
      if (!optionsContainer) {
        return;
      }
      var optionNodes = Array.prototype.slice.call(
        optionsContainer.querySelectorAll("[data-filter-label]")
      );
      if (!optionNodes.length) {
        return;
      }
      var emptyState = dropdown.querySelector("[data-filter-empty]");

      function applyFilter() {
        var query = normalize(searchField.value.trim());
        var matches = 0;
        optionNodes.forEach(function (option) {
          var label =
            option.getAttribute("data-filter-label") ||
            option.textContent ||
            "";
          var shouldShow = !query || normalize(label).indexOf(query) !== -1;
          if (shouldShow) {
            option.classList.remove("hidden");
            matches += 1;
          } else {
            option.classList.add("hidden");
          }
        });
        if (emptyState) {
          if (matches === 0) {
            emptyState.classList.remove("hidden");
          } else {
            emptyState.classList.add("hidden");
          }
        }
      }

      searchField.addEventListener("input", applyFilter);
      dropdown.addEventListener("toggle", function () {
        if (dropdown.open) {
          applyFilter();
          searchField.focus();
        }
      });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    var dropdowns = document.querySelectorAll("[data-filter-dropdown]");
    if (!dropdowns.length) {
      return;
    }
    initDropdownClosing(dropdowns);
    initSearchFilters(dropdowns);
  });
})();

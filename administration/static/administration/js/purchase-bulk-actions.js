function setupPurchaseBulkActions() {
  var form = document.querySelector('[data-purchase-bulk-form]');
  if (!form) {
    return;
  }
  var checkboxes = Array.prototype.slice.call(document.querySelectorAll('[data-purchase-bulk-checkbox]'));
  if (!checkboxes.length) {
    return;
  }
  var masterToggle = document.querySelector('[data-purchase-bulk-toggle]');
  var summary = document.querySelector('[data-purchase-bulk-summary]');
  var countLabel = document.querySelector('[data-purchase-bulk-count]');
  var requiresSelection = Array.prototype.slice.call(
    form.querySelectorAll('[data-purchase-bulk-requires-selection]')
  );
  var panel = document.querySelector('[data-purchase-bulk-panel]');
  var panelToggle = document.querySelector('[data-purchase-bulk-panel-toggle]');
  var panelStateLabel = document.querySelector('[data-purchase-bulk-panel-state]');
  var panelIcon = document.querySelector('[data-purchase-bulk-panel-icon]');
  var collapsedLabel = panelStateLabel ? panelStateLabel.getAttribute('data-collapsed-label') : '';
  var expandedLabel = panelStateLabel ? panelStateLabel.getAttribute('data-expanded-label') : '';
  var panelExpanded = false;

  function updateSummaryText(count) {
    if (!summary) {
      return;
    }
    var emptyText = summary.getAttribute('data-empty-text') || '';
    var selectionTemplate = summary.getAttribute('data-selection-template') || '';
    if (!count) {
      summary.textContent = emptyText;
      return;
    }
    var template = selectionTemplate || emptyText || '';
    summary.textContent = template.replace('__count__', String(count));
  }

  function setControlsDisabled(isDisabled) {
    requiresSelection.forEach(function (element) {
      if ('disabled' in element) {
        try {
          element.disabled = isDisabled;
        } catch (_error) {
          // Some elements (like divs) do not support the disabled property; ignore safely.
        }
      }
    });
  }

  function syncState() {
    var selectedCount = checkboxes.filter(function (checkbox) {
      return !checkbox.disabled && checkbox.checked;
    }).length;
    if (countLabel) {
      countLabel.textContent = String(selectedCount);
    }
    updateSummaryText(selectedCount);
    setControlsDisabled(selectedCount === 0);
    if (masterToggle) {
      masterToggle.indeterminate = selectedCount > 0 && selectedCount < checkboxes.length;
      masterToggle.checked = selectedCount > 0 && selectedCount === checkboxes.length;
    }
  }

  checkboxes.forEach(function (checkbox) {
    checkbox.addEventListener('change', syncState);
  });

  if (masterToggle) {
    masterToggle.addEventListener('change', function () {
      var shouldCheck = Boolean(masterToggle.checked);
      checkboxes.forEach(function (checkbox) {
        if (checkbox.disabled) {
          return;
        }
        checkbox.checked = shouldCheck;
      });
      syncState();
    });
  }

  function setPanelExpanded(expanded) {
    panelExpanded = expanded;
    if (panel) {
      if (expanded) {
        panel.removeAttribute('hidden');
      } else {
        panel.setAttribute('hidden', 'hidden');
      }
    }
    if (panelToggle) {
      panelToggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    }
    if (panelStateLabel) {
      var label = expanded ? expandedLabel : collapsedLabel;
      if (label) {
        panelStateLabel.textContent = label;
      }
    }
    if (panelIcon) {
      panelIcon.classList.toggle('rotate-180', expanded);
    }
  }

  if (panelToggle) {
    panelToggle.addEventListener('click', function () {
      setPanelExpanded(!panelExpanded);
    });
    setPanelExpanded(false);
  } else if (panel) {
    panel.removeAttribute('hidden');
  }

  syncState();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', setupPurchaseBulkActions);
} else {
  setupPurchaseBulkActions();
}

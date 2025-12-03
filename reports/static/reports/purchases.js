(function () {
  const datasetElement = document.getElementById('reports-purchases-data');
  if (!datasetElement || typeof window.Chart === 'undefined') {
    return;
  }

  let payload = {};
  try {
    payload = JSON.parse(datasetElement.textContent || '{}');
  } catch (error) {
    console.warn('No fue posible interpretar los datos de gastos.', error);
    return;
  }

  const ChartJS = window.Chart;
  ChartJS.defaults.font.family =
    'Inter, "Inter var", system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
  ChartJS.defaults.color = '#0f172a';

  const palette = [
    '#0ea5e9',
    '#f97316',
    '#10b981',
    '#6366f1',
    '#ef4444',
    '#14b8a6',
    '#8b5cf6',
    '#ec4899',
  ];

  const renderDoughnut = (canvasId, entries) => {
    const canvas = document.getElementById(canvasId);
    if (!canvas || !Array.isArray(entries) || !entries.length) {
      return;
    }
    new ChartJS(canvas, {
      type: 'doughnut',
      data: {
        labels: entries.map((entry) => entry.label),
        datasets: [
          {
            data: entries.map((entry) => entry.value),
            backgroundColor: entries.map((_, index) => palette[index % palette.length]),
            borderWidth: 0,
          },
        ],
      },
      options: {
        responsive: true,
        cutout: '60%',
        plugins: {
          legend: { position: 'bottom' },
        },
      },
    });
  };

  renderDoughnut('chart-category-share', payload.categoryShare);
  renderDoughnut('chart-payment-share', payload.paymentMethodShare);
  renderDoughnut('chart-requester-share', payload.requesterShare);
  renderDoughnut('chart-support-share', payload.supportShare);

  const renderHorizontalBar = (canvasId, entries) => {
    const canvas = document.getElementById(canvasId);
    if (!canvas || !Array.isArray(entries) || !entries.length) {
      return;
    }
    new ChartJS(canvas, {
      type: 'bar',
      data: {
        labels: entries.map((entry) => entry.label),
        datasets: [
          {
            data: entries.map((entry) => entry.value),
            backgroundColor: '#0ea5e9',
            borderRadius: 6,
          },
        ],
      },
      options: {
        indexAxis: 'y',
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: {
            ticks: {
              callback: (value) => `$${Number(value).toLocaleString()}`,
            },
          },
        },
        plugins: {
          legend: { display: false },
        },
      },
    });
  };

  renderHorizontalBar('chart-area-share-bar', payload.areaShare);

  const timelineCanvas = document.getElementById('chart-spending-timeline');
  if (
    timelineCanvas &&
    payload.timeline &&
    Array.isArray(payload.timeline.labels) &&
    payload.timeline.labels.length
  ) {
    new ChartJS(timelineCanvas, {
      type: 'line',
      data: {
        labels: payload.timeline.labels,
        datasets: [
          {
            label: 'Comprometido',
            data: payload.timeline.committed,
            fill: false,
            tension: 0.35,
            borderColor: '#94a3b8',
            backgroundColor: '#94a3b8',
            borderWidth: 3,
          },
          {
            label: 'Ejecutado',
            data: payload.timeline.executed,
            fill: false,
            tension: 0.35,
            borderColor: '#0ea5e9',
            backgroundColor: '#0ea5e9',
            borderWidth: 3,
          },
        ],
      },
      options: {
        responsive: true,
        interaction: { intersect: false, mode: 'index' },
        scales: {
          y: {
            beginAtZero: true,
            ticks: {
              callback: (value) => `$${Number(value).toLocaleString()}`,
            },
          },
        },
        plugins: {
          legend: { position: 'bottom' },
          tooltip: {
            callbacks: {
              label: (context) => `${context.dataset.label}: $${context.parsed.y.toLocaleString()}`,
            },
          },
        },
      },
    });
  }

  const statusCanvas = document.getElementById('chart-status-share');
  if (
    statusCanvas &&
    payload.statusShare &&
    Array.isArray(payload.statusShare.labels) &&
    payload.statusShare.labels.length
  ) {
    new ChartJS(statusCanvas, {
      type: 'bar',
      data: {
        labels: payload.statusShare.labels,
        datasets: [
          {
            label: 'Monto',
            data: payload.statusShare.amounts,
            backgroundColor: '#f97316',
            borderRadius: 6,
          },
        ],
      },
      options: {
        indexAxis: 'y',
        responsive: true,
        scales: {
          x: {
            ticks: {
              callback: (value) => `$${Number(value).toLocaleString()}`,
            },
          },
        },
        plugins: {
          legend: { display: false },
        },
      },
    });
  }
})();

(function () {
  const datasetElement = document.getElementById('reports-chart-data');
  if (!datasetElement || typeof window.Chart === 'undefined') {
    return;
  }

  let payload = {};
  try {
    payload = JSON.parse(datasetElement.textContent || '{}');
  } catch (error) {
    console.warn('No fue posible interpretar los datos de las gráficas.', error);
    return;
  }

  const ChartJS = window.Chart;
  ChartJS.defaults.font.family =
    'Inter, "Inter var", system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
  ChartJS.defaults.color = '#0f172a';

  const palette = [
    '#f59e0b',
    '#0ea5e9',
    '#10b981',
    '#6366f1',
    '#ef4444',
    '#14b8a6',
    '#a855f7',
  ];

  const priceHistoryCanvas = document.getElementById('chart-price-history');
  if (priceHistoryCanvas && Array.isArray(payload.priceHistory) && payload.priceHistory.length) {
    new ChartJS(priceHistoryCanvas, {
      type: 'line',
      data: {
        datasets: payload.priceHistory.map((series, index) => ({
          label: series.label,
          data: series.data,
          borderColor: palette[index % palette.length],
          backgroundColor: palette[index % palette.length],
          tension: 0.4,
          fill: false,
        })),
      },
      options: {
        responsive: true,
        interaction: { intersect: false, mode: 'index' },
        scales: {
          x: { ticks: { font: { size: 11 } } },
          y: {
            ticks: {
              callback: (value) => `$${value.toLocaleString()}`,
            },
          },
        },
        plugins: {
          legend: { display: true, position: 'bottom' },
          tooltip: {
            callbacks: {
              label: (context) => `${context.dataset.label}: $${context.parsed.y.toFixed(2)}`,
            },
          },
        },
      },
    });
  }

  const dispatchCanvas = document.getElementById('chart-dispatch-vs-sales');
  if (
    dispatchCanvas &&
    payload.dispatchVsSales &&
    Array.isArray(payload.dispatchVsSales.labels) &&
    payload.dispatchVsSales.labels.length
  ) {
    const dispatched = Array.isArray(payload.dispatchVsSales.dispatched)
      ? payload.dispatchVsSales.dispatched
      : [];
    const sold = Array.isArray(payload.dispatchVsSales.sold) ? payload.dispatchVsSales.sold : [];
    new ChartJS(dispatchCanvas, {
      type: 'bar',
      data: {
        labels: payload.dispatchVsSales.labels,
        datasets: [
          {
            label: 'Despachos',
            data: dispatched,
            backgroundColor: '#0ea5e9',
            borderRadius: 6,
          },
          {
            label: 'Ventas',
            data: sold,
            backgroundColor: '#f97316',
            borderRadius: 6,
          },
        ],
      },
      options: {
        responsive: true,
        interaction: { intersect: false, mode: 'index' },
        scales: {
          x: { stacked: false },
          y: {
            beginAtZero: true,
            ticks: {
              callback: (value) => value.toLocaleString(),
            },
          },
        },
        plugins: {
          legend: { display: true, position: 'bottom' },
        },
      },
    });
  }

  const productionCanvas = document.getElementById('chart-production-vs-classification');
  if (
    productionCanvas &&
    payload.productionVsClassification &&
    Array.isArray(payload.productionVsClassification.data)
  ) {
    const classificationLabels = Array.isArray(payload.productionVsClassification.labels)
      ? payload.productionVsClassification.labels
      : ['Reportado', 'Clasificado'];
    new ChartJS(productionCanvas, {
      type: 'bar',
      data: {
        labels: classificationLabels,
        datasets: [
          {
            data: payload.productionVsClassification.data,
            backgroundColor: ['#0ea5e9', '#f59f00'],
            borderRadius: 8,
            barThickness: 30,
          },
        ],
      },
      options: {
        indexAxis: 'y',
        plugins: { legend: { display: false } },
        scales: {
          x: { beginAtZero: true },
          y: { ticks: { font: { size: 12 } } },
        },
      },
    });
  }

  const typeDCanvas = document.getElementById('chart-type-d');
  if (
    typeDCanvas &&
    payload.typeDRatios &&
    Array.isArray(payload.typeDRatios.labels) &&
    payload.typeDRatios.labels.length
  ) {
    new ChartJS(typeDCanvas, {
      type: 'bar',
      data: {
        labels: payload.typeDRatios.labels,
        datasets: [
          {
            label: '% Tipo D',
            data: payload.typeDRatios.values,
            backgroundColor: '#ef4444',
            borderRadius: 6,
          },
        ],
      },
      options: {
        indexAxis: 'y',
        plugins: {
          legend: { display: false },
        },
        scales: {
          x: {
            beginAtZero: true,
            ticks: {
              callback: (value) => `${value}%`,
            },
          },
        },
      },
    });
  }

  const mortalityCanvas = document.getElementById('chart-mortality');
  if (
    mortalityCanvas &&
    payload.mortalityRatios &&
    Array.isArray(payload.mortalityRatios.labels) &&
    payload.mortalityRatios.labels.length
  ) {
    new ChartJS(mortalityCanvas, {
      type: 'bar',
      data: {
        labels: payload.mortalityRatios.labels,
        datasets: [
          {
            label: '% Mortalidad',
            data: payload.mortalityRatios.values,
            backgroundColor: '#14b8a6',
            borderRadius: 6,
          },
        ],
      },
      options: {
        indexAxis: 'y',
        plugins: { legend: { display: false } },
        scales: {
          x: {
            beginAtZero: true,
            ticks: {
              callback: (value) => `${value}%`,
            },
          },
        },
      },
    });
  }
})();

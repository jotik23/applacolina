(function () {
  const datasetElement = document.getElementById('inventory-comparison-data');
  if (!datasetElement || typeof window.Chart === 'undefined') {
    return;
  }

  let payload = {};
  try {
    payload = JSON.parse(datasetElement.textContent || '{}');
  } catch (error) {
    console.warn('No fue posible interpretar los datos del informe de inventarios.', error);
    return;
  }

  const ChartJS = window.Chart;
  ChartJS.defaults.font.family =
    'Inter, "Inter var", system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
  ChartJS.defaults.color = '#0f172a';

  const stageCanvas = document.getElementById('chart-inventory-stages');
  if (
    stageCanvas &&
    payload.stageTotals &&
    Array.isArray(payload.stageTotals.labels) &&
    Array.isArray(payload.stageTotals.data)
  ) {
    new ChartJS(stageCanvas, {
      type: 'bar',
      data: {
        labels: payload.stageTotals.labels,
        datasets: [
          {
            label: 'Cartones',
            data: payload.stageTotals.data,
            backgroundColor: ['#0ea5e9', '#f59e0b', '#38bdf8', '#10b981'],
            borderRadius: 10,
          },
        ],
      },
      options: {
        responsive: true,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (context) => `${context.parsed.y?.toLocaleString()} cartones`,
            },
          },
        },
        scales: {
          y: {
            beginAtZero: true,
            ticks: {
              callback: (value) => value.toLocaleString(),
            },
          },
        },
      },
    });
  }

  const typeCanvas = document.getElementById('chart-type-comparison');
  if (
    typeCanvas &&
    payload.typeComparative &&
    Array.isArray(payload.typeComparative.labels)
  ) {
    const labels = payload.typeComparative.labels;
    const classified = Array.isArray(payload.typeComparative.classified)
      ? payload.typeComparative.classified
      : [];
    const dispatched = Array.isArray(payload.typeComparative.dispatched)
      ? payload.typeComparative.dispatched
      : [];
    const sold = Array.isArray(payload.typeComparative.sold) ? payload.typeComparative.sold : [];

    new ChartJS(typeCanvas, {
      type: 'bar',
      data: {
        labels,
        datasets: [
          {
            label: 'Clasificado',
            data: classified,
            backgroundColor: '#10b981',
            borderRadius: 6,
          },
          {
            label: 'Despachado',
            data: dispatched,
            backgroundColor: '#0ea5e9',
            borderRadius: 6,
          },
          {
            label: 'Vendido',
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
})();

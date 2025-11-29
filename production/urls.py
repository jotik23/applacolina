from django.urls import path
from django.views.generic import RedirectView

from .views import (
    egg_classification_shift_summary_view,
    daily_indicators_view,
    egg_inventory_batch_detail_view,
    egg_inventory_cardex_view,
    egg_inventory_dashboard_view,
)

app_name = "production"

urlpatterns = [
    path("", daily_indicators_view, name="index"),
    path("indicadores-dia/", daily_indicators_view, name="daily-indicators"),
    path("inventario-huevo/", egg_inventory_dashboard_view, name="egg-inventory"),
    path(
        "inventario-huevo/cardex/",
        egg_inventory_cardex_view,
        name="egg-inventory-cardex",
    ),
    path(
        "inventario-huevo/clasificacion-turno/",
        egg_classification_shift_summary_view,
        name="egg-classification-shift-summary",
    ),
    path(
        "inventario-huevo/lote/<int:pk>/",
        egg_inventory_batch_detail_view,
        name="egg-inventory-batch",
    ),
    path(
        "lotes/",
        RedirectView.as_view(pattern_name="configuration:batches", permanent=False),
        name="batches",
    ),
    path(
        "lotes/<int:pk>/produccion/",
        RedirectView.as_view(pattern_name="configuration:batch-production-board", permanent=False),
        name="batch-production-board",
    ),
    path(
        "lotes/<int:pk>/editar/",
        RedirectView.as_view(pattern_name="configuration:batch-update", permanent=False),
        name="batch-update",
    ),
    path(
        "lotes/<int:pk>/eliminar/",
        RedirectView.as_view(pattern_name="configuration:batch-delete", permanent=False),
        name="batch-delete",
    ),
    path(
        "lotes/asignaciones/<int:pk>/eliminar/",
        RedirectView.as_view(pattern_name="configuration:batch-allocation-delete", permanent=False),
        name="batch-allocation-delete",
    ),
    path(
        "infraestructura/",
        RedirectView.as_view(pattern_name="configuration:infrastructure", permanent=False),
        name="infrastructure",
    ),
    path(
        "tablas-referencia/",
        RedirectView.as_view(pattern_name="configuration:reference-tables", permanent=False),
        name="reference-tables",
    ),
    path(
        "infraestructura/granjas/<int:pk>/editar/",
        RedirectView.as_view(pattern_name="configuration:farm-update", permanent=False),
        name="farm-update",
    ),
    path(
        "infraestructura/granjas/<int:pk>/eliminar/",
        RedirectView.as_view(pattern_name="configuration:farm-delete", permanent=False),
        name="farm-delete",
    ),
    path(
        "infraestructura/galpones/<int:pk>/editar/",
        RedirectView.as_view(pattern_name="configuration:chicken-house-update", permanent=False),
        name="chicken-house-update",
    ),
    path(
        "infraestructura/galpones/<int:pk>/eliminar/",
        RedirectView.as_view(pattern_name="configuration:chicken-house-delete", permanent=False),
        name="chicken-house-delete",
    ),
    path(
        "infraestructura/salones/<int:pk>/editar/",
        RedirectView.as_view(pattern_name="configuration:room-update", permanent=False),
        name="room-update",
    ),
    path(
        "infraestructura/salones/<int:pk>/eliminar/",
        RedirectView.as_view(pattern_name="configuration:room-delete", permanent=False),
        name="room-delete",
    ),
]

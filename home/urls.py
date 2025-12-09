from django.urls import path
from django.views.generic import RedirectView

from administration.views import (
    AdministrationHomeView,
    EggDispatchListView,
    PayrollManagementView,
    SalesCardexView,
    SalesDashboardView,
    SupplierManagementView,
)
from production.views import (
    batch_production_board_view,
    daily_indicators_view,
    egg_inventory_dashboard_view,
)
from inventory.views import HomeInventoryDashboardView
from task_manager.views import task_manager_daily_report_view

app_name = "home"

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="home:sales", permanent=False), name="index"),
    path("ventas/", SalesDashboardView.as_view(), name="sales"),
    path("ventas/cardex/", SalesCardexView.as_view(), name="sales-cardex"),
    path("despachos/", EggDispatchListView.as_view(), name="dispatches"),
    path("clasificacion-inventario/", egg_inventory_dashboard_view, name="egg-inventory"),
    path("produccion-indicadores/", daily_indicators_view, name="daily-indicators"),
    path(
        "produccion/lotes/<int:pk>/produccion/",
        batch_production_board_view,
        name="batch-production-board",
    ),
    path("reporte-tareas/", task_manager_daily_report_view, name="task-report"),
    path("compras/", AdministrationHomeView.as_view(), name="purchases"),
    path("inventario/", HomeInventoryDashboardView.as_view(), name="inventory"),
    path("nomina/", PayrollManagementView.as_view(), name="payroll"),
    path("terceros/", SupplierManagementView.as_view(), name="suppliers"),
]

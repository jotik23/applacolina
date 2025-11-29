from django.urls import path
from django.views.generic import RedirectView

from administration.views import (
    AdministrationHomeView,
    EggDispatchListView,
    PayrollManagementView,
    SalesDashboardView,
    SupplierManagementView,
)
from production.views import daily_indicators_view, egg_inventory_dashboard_view
from task_manager.views import task_manager_daily_report_view

app_name = "home"

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="home:sales", permanent=False), name="index"),
    path("ventas/", SalesDashboardView.as_view(), name="sales"),
    path("despachos/", EggDispatchListView.as_view(), name="dispatches"),
    path("clasificacion-inventario/", egg_inventory_dashboard_view, name="egg-inventory"),
    path("produccion-indicadores/", daily_indicators_view, name="daily-indicators"),
    path("reporte-tareas/", task_manager_daily_report_view, name="task-report"),
    path("compras/", AdministrationHomeView.as_view(), name="purchases"),
    path("nomina/", PayrollManagementView.as_view(), name="payroll"),
    path("terceros/", SupplierManagementView.as_view(), name="suppliers"),
]

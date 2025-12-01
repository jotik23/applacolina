from django.urls import path
from django.views.generic import RedirectView

from .views import (
    AdministrationHomeView,
    EggDispatchCreateView,
    EggDispatchDeleteView,
    EggDispatchListView,
    EggDispatchUpdateView,
    PayrollManagementView,
    SaleCreateView,
    SalesPaymentListView,
    SaleUpdateView,
    SalesCardexView,
    SalesDashboardView,
    SupplierImportTemplateView,
    SupplierManagementView,
    SupplierQuickCreateView,
)

app_name = 'administration'

urlpatterns = [
    path('', RedirectView.as_view(pattern_name='administration:sales', permanent=False), name='index'),
    path('ventas/', SalesDashboardView.as_view(), name='sales'),
    path('ventas/abonos/', SalesPaymentListView.as_view(), name='sales-payments'),
    path('ventas/cardex/', SalesCardexView.as_view(), name='sales-cardex'),
    path('ventas/nueva/', SaleCreateView.as_view(), name='sale-create'),
    path('ventas/<int:pk>/editar/', SaleUpdateView.as_view(), name='sale-update'),
    path('despachos/', EggDispatchListView.as_view(), name='egg-dispatch-list'),
    path('despachos/nuevo/', EggDispatchCreateView.as_view(), name='egg-dispatch-create'),
    path('despachos/<int:pk>/editar/', EggDispatchUpdateView.as_view(), name='egg-dispatch-update'),
    path('despachos/<int:pk>/eliminar/', EggDispatchDeleteView.as_view(), name='egg-dispatch-delete'),
    path('compras/', AdministrationHomeView.as_view(), name='purchases'),
    path(
        'compras/productos/',
        RedirectView.as_view(pattern_name='configuration:products', permanent=False),
        name='purchases_products',
    ),
    path('compras/nomina/', PayrollManagementView.as_view(), name='purchases_payroll'),
    path('compras/proveedores/', SupplierManagementView.as_view(), name='purchases_suppliers'),
    path('compras/proveedores/quick-create/', SupplierQuickCreateView.as_view(), name='purchases_supplier_quick_create'),
    path(
        'compras/proveedores/import-template/',
        SupplierImportTemplateView.as_view(),
        name='purchases_supplier_import_template',
    ),
    path(
        'compras/configuracion/',
        RedirectView.as_view(pattern_name='configuration:expense-configuration', permanent=False),
        name='purchases_configuration',
    ),
]

from django.urls import path
from django.views.generic import RedirectView

from .views import (
    AdministrationHomeView,
    EggDispatchCreateView,
    EggDispatchDeleteView,
    EggDispatchListView,
    EggDispatchUpdateView,
    PayrollManagementView,
    ProductManagementView,
    PurchaseConfigurationView,
    SupplierImportTemplateView,
    SupplierManagementView,
    SupplierQuickCreateView,
)

app_name = 'administration'

urlpatterns = [
    path('', RedirectView.as_view(pattern_name='administration:purchases', permanent=False), name='index'),
    path('despachos/', EggDispatchListView.as_view(), name='egg-dispatch-list'),
    path('despachos/nuevo/', EggDispatchCreateView.as_view(), name='egg-dispatch-create'),
    path('despachos/<int:pk>/editar/', EggDispatchUpdateView.as_view(), name='egg-dispatch-update'),
    path('despachos/<int:pk>/eliminar/', EggDispatchDeleteView.as_view(), name='egg-dispatch-delete'),
    path('compras/', AdministrationHomeView.as_view(), name='purchases'),
    path('compras/productos/', ProductManagementView.as_view(), name='purchases_products'),
    path('compras/nomina/', PayrollManagementView.as_view(), name='purchases_payroll'),
    path('compras/proveedores/', SupplierManagementView.as_view(), name='purchases_suppliers'),
    path('compras/proveedores/quick-create/', SupplierQuickCreateView.as_view(), name='purchases_supplier_quick_create'),
    path(
        'compras/proveedores/import-template/',
        SupplierImportTemplateView.as_view(),
        name='purchases_supplier_import_template',
    ),
    path('compras/configuracion/', PurchaseConfigurationView.as_view(), name='purchases_configuration'),
]

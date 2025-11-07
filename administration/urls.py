from django.urls import path
from django.views.generic import RedirectView

from .views import (
    AdministrationHomeView,
    PurchaseConfigurationView,
    SupplierManagementView,
    SupplierQuickCreateView,
)

app_name = 'administration'

urlpatterns = [
    path('', RedirectView.as_view(pattern_name='administration:purchases', permanent=False), name='index'),
    path('compras/', AdministrationHomeView.as_view(), name='purchases'),
    path('compras/proveedores/', SupplierManagementView.as_view(), name='purchases_suppliers'),
    path('compras/proveedores/quick-create/', SupplierQuickCreateView.as_view(), name='purchases_supplier_quick_create'),
    path('compras/configuracion/', PurchaseConfigurationView.as_view(), name='purchases_configuration'),
]

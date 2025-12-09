from django.urls import path

from .views import InventoryDashboardView

app_name = "inventory"

urlpatterns = [
    path("", InventoryDashboardView.as_view(), name="dashboard"),
]

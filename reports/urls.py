from django.urls import path

from .views import InventoryComparisonView, KeyMetricsDashboardView

app_name = "reports"

urlpatterns = [
    path("", KeyMetricsDashboardView.as_view(), name="dashboard"),
    path(
        "inventarios/",
        InventoryComparisonView.as_view(),
        name="inventory-comparison",
    ),
]

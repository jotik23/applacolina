from django.urls import path

from .views import KeyMetricsDashboardView

app_name = "reports"

urlpatterns = [
    path("", KeyMetricsDashboardView.as_view(), name="dashboard"),
]


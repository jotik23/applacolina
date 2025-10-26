from django.urls import path

from .views import (
    CalendarConfiguratorView,
    CalendarDashboardView,
    CalendarDeleteView,
    CalendarDetailView,
    CalendarRulesView,
)


app_name = "calendario"

urlpatterns = [
    path("configurar/", CalendarConfiguratorView.as_view(), name="configurator"),
    path("reglas/", CalendarRulesView.as_view(), name="rules"),
    path("", CalendarDashboardView.as_view(), name="dashboard"),
    path("calendars/<int:pk>/", CalendarDetailView.as_view(), name="calendar-detail"),
    path("calendars/<int:pk>/delete/", CalendarDeleteView.as_view(), name="calendar-delete"),
]

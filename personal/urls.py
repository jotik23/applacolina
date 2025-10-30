from django.urls import path

from .views import (
    CalendarConfiguratorView,
    CalendarDashboardView,
    CalendarCreateView,
    CalendarDeleteView,
    CalendarDetailView,
)


app_name = "personal"

urlpatterns = [
    path("configurar/", CalendarConfiguratorView.as_view(), name="configurator"),
    path("", CalendarDashboardView.as_view(), name="dashboard"),
    path("calendars/create/", CalendarCreateView.as_view(), name="calendar-create"),
    path("calendars/<int:pk>/", CalendarDetailView.as_view(), name="calendar-detail"),
    path("calendars/<int:pk>/delete/", CalendarDeleteView.as_view(), name="calendar-delete"),
]

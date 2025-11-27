from django.urls import path

from .views import (
    CalendarConfiguratorView,
    CalendarDashboardView,
    CalendarCreateView,
    CalendarDeleteView,
    CalendarDetailView,
    CalendarPublicShareView,
    CalendarSharePreviewView,
)


app_name = "personal"

urlpatterns = [
    path("configurar/", CalendarConfiguratorView.as_view(), name="configurator"),
    path("", CalendarDashboardView.as_view(), name="dashboard"),
    path("calendars/create/", CalendarCreateView.as_view(), name="calendar-create"),
    path("calendars/<int:pk>/", CalendarDetailView.as_view(), name="calendar-detail"),
    path("shared/calendars/<int:pk>/", CalendarPublicShareView.as_view(), name="calendar-public-share"),
    path("calendars/<int:pk>/share-preview/", CalendarSharePreviewView.as_view(), name="calendar-share-preview"),
    path("calendars/<int:pk>/delete/", CalendarDeleteView.as_view(), name="calendar-delete"),
]

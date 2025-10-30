from __future__ import annotations

from django.urls import path

from .views import CalendarLogoutView, CalendarPortalView


app_name = "portal"

urlpatterns = [
    path("", CalendarPortalView.as_view(), name="login"),
    path("logout/", CalendarLogoutView.as_view(), name="logout"),
]

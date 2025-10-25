from django.urls import path

from .views import CalendarDashboardView, CalendarDetailView


app_name = "calendario"

urlpatterns = [
    path("", CalendarDashboardView.as_view(), name="dashboard"),
    path("calendars/<int:pk>/", CalendarDetailView.as_view(), name="calendar-detail"),
]

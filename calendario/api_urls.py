from django.urls import path

from .views import CalendarApproveView, CalendarGenerateView, CalendarListView


app_name = "calendario-api"

urlpatterns = [
    path("calendars/", CalendarListView.as_view(), name="calendar-list"),
    path("calendars/generate/", CalendarGenerateView.as_view(), name="calendar-generate"),
    path("calendars/<int:calendar_id>/approve/", CalendarApproveView.as_view(), name="calendar-approve"),
]

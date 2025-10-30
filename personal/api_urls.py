from django.urls import path

from .views import (
    CalendarApproveView,
    CalendarAssignmentCollectionView,
    CalendarAssignmentDetailView,
    CalendarEligibleOperatorsView,
    CalendarGenerateView,
    CalendarListView,
    CalendarMetadataView,
    CalendarSummaryView,
    RestPeriodCollectionView,
    RestPeriodDetailView,
    OperatorCollectionView,
    OperatorDetailView,
    PositionCollectionView,
    PositionDetailView,
    PositionReorderView,
)


app_name = "personal-api"

urlpatterns = [
    path("calendars/", CalendarListView.as_view(), name="calendar-list"),
    path("calendars/generate/", CalendarGenerateView.as_view(), name="calendar-generate"),
    path("calendars/<int:calendar_id>/approve/", CalendarApproveView.as_view(), name="calendar-approve"),
    path("calendars/metadata/", CalendarMetadataView.as_view(), name="calendar-metadata"),
    path(
        "calendars/<int:calendar_id>/summary/",
        CalendarSummaryView.as_view(),
        name="calendar-summary",
    ),
    path(
        "calendars/<int:calendar_id>/assignments/",
        CalendarAssignmentCollectionView.as_view(),
        name="calendar-assignments",
    ),
    path(
        "calendars/<int:calendar_id>/assignments/<int:assignment_id>/",
        CalendarAssignmentDetailView.as_view(),
        name="calendar-assignment-detail",
    ),
    path(
        "calendars/<int:calendar_id>/eligible-operators/",
        CalendarEligibleOperatorsView.as_view(),
        name="calendar-eligible-operators",
    ),
    path("calendars/operators/", OperatorCollectionView.as_view(), name="calendar-operators"),
    path(
        "calendars/operators/<int:operator_id>/",
        OperatorDetailView.as_view(),
        name="calendar-operator-detail",
    ),
    path("calendars/positions/", PositionCollectionView.as_view(), name="calendar-positions"),
    path(
        "calendars/positions/<int:position_id>/",
        PositionDetailView.as_view(),
        name="calendar-position-detail",
    ),
    path(
        "calendars/positions/reorder/",
        PositionReorderView.as_view(),
        name="calendar-position-reorder",
    ),
    path(
        "calendars/rest-periods/",
        RestPeriodCollectionView.as_view(),
        name="calendar-rest-periods",
    ),
    path(
        "calendars/rest-periods/<int:rest_period_id>/",
        RestPeriodDetailView.as_view(),
        name="calendar-rest-period-detail",
    ),
]

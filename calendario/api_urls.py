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
    CapabilityCollectionView,
    CapabilityDetailView,
    OperatorCollectionView,
    OperatorDetailView,
    OverloadCollectionView,
    OverloadDetailView,
    PositionCollectionView,
    PositionDetailView,
    RestRuleCollectionView,
    RestRuleDetailView,
)


app_name = "calendario-api"

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
    path("calendars/capabilities/", CapabilityCollectionView.as_view(), name="calendar-capabilities"),
    path(
        "calendars/capabilities/<int:capability_id>/",
        CapabilityDetailView.as_view(),
        name="calendar-capability-detail",
    ),
    path("calendars/rest-rules/", RestRuleCollectionView.as_view(), name="calendar-rest-rules"),
    path(
        "calendars/rest-rules/<int:rule_id>/",
        RestRuleDetailView.as_view(),
        name="calendar-rest-rule-detail",
    ),
    path("calendars/overload-rules/", OverloadCollectionView.as_view(), name="calendar-overload-rules"),
    path(
        "calendars/overload-rules/<int:overload_id>/",
        OverloadDetailView.as_view(),
        name="calendar-overload-rule-detail",
    ),
]

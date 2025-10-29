from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable, List, Optional

from django.db import transaction
from django.db.models import Q

from ..models import AssignmentAlertLevel, AssignmentDecision, PositionDefinition, RestPeriodSource, ShiftCalendar


@dataclass(slots=True)
class SchedulerOptions:
    """Kept for API compatibility; flags are currently ignored."""

    honor_rest_rules: bool = False
    honor_preferences: bool = False
    replace_existing: bool = True


class CalendarScheduler:
    """Minimal scheduler that only produces placeholder gaps for the UI."""

    def __init__(self, calendar: ShiftCalendar, *, options: Optional[SchedulerOptions] = None) -> None:
        if calendar.start_date > calendar.end_date:
            raise ValueError("El calendario tiene un rango inválido.")
        self.calendar = calendar
        self.options = options or SchedulerOptions()
        self._calendar_dates = list(self._daterange(calendar.start_date, calendar.end_date))
        self._positions = self._load_positions()

    def generate(self, *, commit: bool = False) -> List[AssignmentDecision]:
        decisions: List[AssignmentDecision] = []
        for current_date in self._calendar_dates:
            for position in self._positions:
                if not position.is_active_on(current_date):
                    continue
                decisions.append(
                    AssignmentDecision(
                        position=position,
                        operator=None,
                        date=current_date,
                        alert_level=AssignmentAlertLevel.CRITICAL,
                        is_overtime=False,
                        notes="Asignación pendiente: la generación automática está deshabilitada.",
                    )
                )

        if commit:
            with transaction.atomic():
                self._reset_auto_assignments()
                self._clear_workload_snapshots()
                self._clear_calendar_rest_periods()

        return decisions

    def _load_positions(self) -> List[PositionDefinition]:
        return list(
            PositionDefinition.objects.select_related("farm", "chicken_house", "category")
            .prefetch_related("rooms")
            .filter(valid_from__lte=self.calendar.end_date)
            .filter(Q(valid_until__isnull=True) | Q(valid_until__gte=self.calendar.start_date))
            .order_by("display_order", "id")
        )

    def _reset_auto_assignments(self) -> None:
        self.calendar.assignments.filter(is_auto_assigned=True).delete()

    def _clear_workload_snapshots(self) -> None:
        self.calendar.workload_snapshots.all().delete()

    def _rebuild_workload_snapshots(self) -> None:
        self._clear_workload_snapshots()

    def _clear_calendar_rest_periods(self) -> None:
        self.calendar.rest_periods.filter(source=RestPeriodSource.CALENDAR).delete()

    @staticmethod
    def _daterange(start: date, end: date) -> Iterable[date]:
        current = start
        while current <= end:
            yield current
            current += timedelta(days=1)


def sync_calendar_rest_periods(
    calendar: ShiftCalendar,
    *,
    operator_ids: Optional[Iterable[int]] = None,
    calendar_dates: Optional[Iterable[date]] = None,
) -> None:
    """Placeholder to preserve import paths while the algorithm is rebuilt."""
    _ = (calendar, operator_ids, calendar_dates)

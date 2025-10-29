from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import DefaultDict, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from django.db import transaction
from django.db.models import Q

from ..models import (
    AssignmentAlertLevel,
    AssignmentDecision,
    CalendarStatus,
    OperatorRestPeriod,
    PositionDefinition,
    RestPeriodSource,
    RestPeriodStatus,
    ShiftAssignment,
    ShiftCalendar,
)
from users.models import UserProfile


OperatorId = int
PositionId = int


@dataclass(slots=True)
class SchedulerOptions:
    """Opciones del generador (se mantienen para compatibilidad)."""

    honor_rest_rules: bool = True
    honor_preferences: bool = True
    replace_existing: bool = True


class CalendarScheduler:
    """Generador que aplica las reglas de turnos y descansos."""

    def __init__(self, calendar: ShiftCalendar, *, options: Optional[SchedulerOptions] = None) -> None:
        if calendar.start_date > calendar.end_date:
            raise ValueError("El calendario tiene un rango inválido.")

        self.calendar = calendar
        self.options = options or SchedulerOptions()
        self._planned_rest_days: DefaultDict[OperatorId, Set[date]] = defaultdict(set)

        self._calendar_dates = list(self._daterange(calendar.start_date, calendar.end_date))
        self._positions = self._load_positions()
        self._position_candidates, self._operator_cache = self._load_position_candidates()
        self._automatic_rest_index: Dict[OperatorId, Set[int]] = {
            operator_id: set(operator.automatic_rest_days or [])
            for operator_id, operator in self._operator_cache.items()
        }
        self._manual_rest_index = self._build_manual_rest_index()
        self._position_history = self._load_position_history()
        self._candidate_priority = self._build_candidate_priority()

    def generate(self, *, commit: bool = False) -> List[AssignmentDecision]:
        self._validate_calendar_range()
        self._planned_rest_days.clear()

        decisions: List[AssignmentDecision] = []
        assigned_per_day: DefaultDict[date, Set[OperatorId]] = defaultdict(set)
        dynamic_rest_blocks: DefaultDict[OperatorId, Set[date]] = defaultdict(set)
        position_last_operator: Dict[PositionId, OperatorId] = {}

        for current_date in self._calendar_dates:
            for position in self._positions:
                position_id = position.id
                if position_id is None or not position.is_active_on(current_date):
                    continue

                operator = self._select_operator(
                    position=position,
                    target_date=current_date,
                    assigned_per_day=assigned_per_day,
                    dynamic_rest_blocks=dynamic_rest_blocks,
                    position_last_operator=position_last_operator,
                )

                if operator is None:
                    decisions.append(
                        AssignmentDecision(
                            position=position,
                            operator=None,
                            date=current_date,
                            alert_level=AssignmentAlertLevel.CRITICAL,
                            notes="Sin operarios elegibles tras aplicar reglas.",
                        )
                    )
                    continue

                decisions.append(
                    AssignmentDecision(
                        position=position,
                        operator=operator,
                        date=current_date,
                        alert_level=AssignmentAlertLevel.NONE,
                    )
                )

                assigned_per_day[current_date].add(operator.id)
                position_last_operator[position_id] = operator.id
                self._schedule_post_shift_rest(
                    operator_id=operator.id,
                    category=position.category,
                    work_date=current_date,
                    dynamic_rest_blocks=dynamic_rest_blocks,
                )

        if commit:
            self._commit_decisions(decisions)

        return decisions

    # ------------------------------------------------------------------ #
    # Datos precargados
    # ------------------------------------------------------------------ #

    def _validate_calendar_range(self) -> None:
        overlap_exists = (
            ShiftCalendar.objects.filter(
                start_date__lte=self.calendar.end_date,
                end_date__gte=self.calendar.start_date,
                status__in=[
                    CalendarStatus.DRAFT,
                    CalendarStatus.APPROVED,
                    CalendarStatus.MODIFIED,
                ],
            )
            .exclude(pk=self.calendar.pk)
            .exists()
        )
        if overlap_exists:
            raise ValueError("El rango del calendario se solapa con otro calendario existente.")

    def _load_positions(self) -> List[PositionDefinition]:
        return list(
            PositionDefinition.objects.select_related("farm", "chicken_house", "category")
            .prefetch_related("rooms")
            .filter(valid_from__lte=self.calendar.end_date)
            .filter(Q(valid_until__isnull=True) | Q(valid_until__gte=self.calendar.start_date))
            .order_by("display_order", "id")
        )

    def _load_position_candidates(self) -> tuple[Dict[PositionId, List[OperatorId]], Dict[OperatorId, UserProfile]]:
        position_ids = [position.id for position in self._positions if position.id]
        if not position_ids:
            return {}, {}

        operator_qs = (
            UserProfile.objects.filter(is_active=True, suggested_positions__in=position_ids)
            .prefetch_related("suggested_positions")
            .distinct()
        )

        operator_cache: Dict[OperatorId, UserProfile] = {operator.id: operator for operator in operator_qs}
        candidates: DefaultDict[PositionId, List[OperatorId]] = defaultdict(list)

        for operator in operator_cache.values():
            for suggested in operator.suggested_positions.all():
                if suggested.id in position_ids:
                    candidates[suggested.id].append(operator.id)

        for position_id, operator_ids in candidates.items():
            operator_ids.sort(
                key=lambda operator_id: (
                    (operator_cache[operator_id].apellidos or "").strip().lower(),
                    (operator_cache[operator_id].nombres or "").strip().lower(),
                    operator_id,
                )
            )

        return dict(candidates), operator_cache

    def _build_manual_rest_index(self) -> Dict[OperatorId, Set[date]]:
        if not self._operator_cache:
            return {}

        operator_ids = list(self._operator_cache.keys())
        rest_qs = OperatorRestPeriod.objects.filter(
            operator_id__in=operator_ids,
            start_date__lte=self.calendar.end_date,
            end_date__gte=self.calendar.start_date,
        ).exclude(status=RestPeriodStatus.CANCELLED)

        rest_index: DefaultDict[OperatorId, Set[date]] = defaultdict(set)
        for period in rest_qs:
            operator_id = period.operator_id
            if not operator_id:
                continue

            start = max(period.start_date, self.calendar.start_date)
            end = min(period.end_date, self.calendar.end_date)
            for rest_day in self._daterange(start, end):
                rest_index[operator_id].add(rest_day)

        return dict(rest_index)

    def _load_position_history(self) -> Dict[PositionId, Dict[OperatorId, Tuple[date, Optional[datetime]]]]:
        position_ids = [position.id for position in self._positions if position.id]
        if not position_ids:
            return {}

        assignment_qs = (
            ShiftAssignment.objects.filter(position_id__in=position_ids, date__lte=self.calendar.end_date)
            .select_related("operator")
            .order_by("date", "updated_at")
        )

        history: DefaultDict[PositionId, Dict[OperatorId, Tuple[date, Optional[datetime]]]] = defaultdict(dict)

        for assignment in assignment_qs:
            operator_id = assignment.operator_id
            position_id = assignment.position_id

            if not operator_id or not position_id:
                continue

            recorded = history[position_id].get(operator_id)
            last_date = assignment.date
            last_updated = assignment.updated_at

            if recorded:
                stored_date, stored_updated = recorded
                if last_date < stored_date:
                    continue
                if (
                    last_date == stored_date
                    and stored_updated
                    and last_updated
                    and last_updated <= stored_updated
                ):
                    continue

            history[position_id][operator_id] = (last_date, last_updated)

        return dict(history)

    def _build_candidate_priority(self) -> Dict[PositionId, List[OperatorId]]:
        priority_map: Dict[PositionId, List[OperatorId]] = {}

        for position in self._positions:
            position_id = position.id
            if position_id is None:
                continue

            base_candidates = self._position_candidates.get(position_id, [])
            if not base_candidates:
                priority_map[position_id] = []
                continue

            history_for_position = self._position_history.get(position_id, {})

            def priority_key(operator_id: int) -> tuple:
                history = history_for_position.get(operator_id)
                operator = self._operator_cache.get(operator_id)
                name_key = (
                    (operator.apellidos or "").strip().lower(),
                    (operator.nombres or "").strip().lower(),
                    operator_id,
                ) if operator else ("", "", operator_id)

                if not history:
                    return (1, 0, 0, name_key)

                last_date, last_updated = history
                updated_ts = int(last_updated.timestamp()) if last_updated else 0
                return (0, -last_date.toordinal(), -updated_ts, name_key)

            priority_map[position_id] = sorted(base_candidates, key=priority_key)

        return priority_map

    # ------------------------------------------------------------------ #
    # Selección de operadores
    # ------------------------------------------------------------------ #

    def _select_operator(
        self,
        *,
        position: PositionDefinition,
        target_date: date,
        assigned_per_day: DefaultDict[date, Set[OperatorId]],
        dynamic_rest_blocks: DefaultDict[OperatorId, Set[date]],
        position_last_operator: Dict[PositionId, OperatorId],
    ) -> Optional[UserProfile]:
        candidate_order: List[OperatorId] = []

        last_operator_id = position_last_operator.get(position.id or -1)
        if last_operator_id:
            candidate_order.append(last_operator_id)

        for operator_id in self._candidate_priority.get(position.id or -1, []):
            if operator_id not in candidate_order:
                candidate_order.append(operator_id)

        for operator_id in candidate_order:
            operator = self._operator_cache.get(operator_id)
            if not operator:
                continue

            if self._is_operator_available(
                operator=operator,
                position=position,
                target_date=target_date,
                assigned_per_day=assigned_per_day,
                dynamic_rest_blocks=dynamic_rest_blocks,
            ):
                return operator

        return None

    def _is_operator_available(
        self,
        *,
        operator: UserProfile,
        position: PositionDefinition,
        target_date: date,
        assigned_per_day: DefaultDict[date, Set[OperatorId]],
        dynamic_rest_blocks: DefaultDict[OperatorId, Set[date]],
    ) -> bool:
        operator_id = operator.id
        if operator_id is None:
            return False

        if not operator.is_active:
            return False

        if not operator.is_active_on(target_date):
            return False

        if operator_id in assigned_per_day.get(target_date, set()):
            return False

        if target_date in self._manual_rest_index.get(operator_id, set()):
            return False

        if target_date in dynamic_rest_blocks.get(operator_id, set()):
            return False

        automatic_days = self._automatic_rest_index.get(operator_id)
        if automatic_days and target_date.weekday() in automatic_days:
            self._planned_rest_days[operator_id].add(target_date)
            return False

        _ = position  # Reserva para reglas futuras basadas en categoría.
        return True

    def _schedule_post_shift_rest(
        self,
        *,
        operator_id: OperatorId,
        category,
        work_date: date,
        dynamic_rest_blocks: DefaultDict[OperatorId, Set[date]],
    ) -> None:
        rest_span = getattr(category, "rest_post_shift_days", 0) or 0
        if rest_span <= 0:
            return

        for offset in range(1, rest_span + 1):
            target_day = work_date + timedelta(days=offset)
            if target_day < self.calendar.start_date or target_day > self.calendar.end_date:
                continue
            dynamic_rest_blocks[operator_id].add(target_day)
            self._planned_rest_days[operator_id].add(target_day)

    # ------------------------------------------------------------------ #
    # Persistencia
    # ------------------------------------------------------------------ #

    def _commit_decisions(self, decisions: Sequence[AssignmentDecision]) -> None:
        with transaction.atomic():
            self._reset_auto_assignments()

            new_assignments: List[ShiftAssignment] = []
            for decision in decisions:
                if decision.operator is None:
                    continue
                new_assignments.append(
                    ShiftAssignment(
                        calendar=self.calendar,
                        position=decision.position,
                        date=decision.date,
                        operator=decision.operator,
                        alert_level=decision.alert_level,
                        is_auto_assigned=True,
                        is_overtime=decision.is_overtime,
                        overtime_points=decision.overtime_points if decision.is_overtime else 0,
                        notes=decision.notes,
                    )
                )

            if new_assignments:
                ShiftAssignment.objects.bulk_create(new_assignments)

            self._clear_workload_snapshots()
            self._clear_calendar_rest_periods()
            self._persist_calendar_rest_periods()

    def _reset_auto_assignments(self) -> None:
        self.calendar.assignments.all().delete()

    def _clear_workload_snapshots(self) -> None:
        self.calendar.workload_snapshots.all().delete()

    def _rebuild_workload_snapshots(self) -> None:
        self._clear_workload_snapshots()

    def _clear_calendar_rest_periods(self) -> None:
        self.calendar.rest_periods.filter(source=RestPeriodSource.CALENDAR).delete()

    def _persist_calendar_rest_periods(self) -> None:
        if not self._planned_rest_days:
            return

        rest_records: List[OperatorRestPeriod] = []
        for operator_id, day_set in self._planned_rest_days.items():
            if not day_set:
                continue
            for start_date, end_date in self._merge_consecutive_days(sorted(day_set)):
                rest_records.append(
                    OperatorRestPeriod(
                        operator_id=operator_id,
                        start_date=start_date,
                        end_date=end_date,
                        status=RestPeriodStatus.PLANNED,
                        source=RestPeriodSource.CALENDAR,
                        calendar=self.calendar,
                        notes="Descanso automático.",
                    )
                )

        if rest_records:
            OperatorRestPeriod.objects.bulk_create(rest_records, ignore_conflicts=True)

    @staticmethod
    def _merge_consecutive_days(days: Sequence[date]) -> List[Tuple[date, date]]:
        if not days:
            return []

        ranges: List[Tuple[date, date]] = []
        current_start = days[0]
        current_end = days[0]

        for current_day in days[1:]:
            if current_day == current_end + timedelta(days=1):
                current_end = current_day
            else:
                ranges.append((current_start, current_end))
                current_start = current_day
                current_end = current_day

        ranges.append((current_start, current_end))
        return ranges

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
    """Marcador de posición: el sincronizador se implementará en iteraciones futuras."""
    _ = (calendar, operator_ids, calendar_dates)

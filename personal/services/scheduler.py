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
    ShiftType,
    UserProfile,
)


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
        self._post_shift_rest_by_assignment: DefaultDict[Tuple[OperatorId, date, PositionId], Set[date]] = defaultdict(set)

        self._calendar_dates = list(self._daterange(calendar.start_date, calendar.end_date))
        self._positions = self._load_positions()
        self._position_index: Dict[PositionId, PositionDefinition] = {
            position.id: position for position in self._positions if position.id is not None
        }
        self._position_candidates, self._operator_cache = self._load_position_candidates()
        self._operator_shift_catalog = self._build_operator_shift_catalog()
        self._automatic_rest_index: Dict[OperatorId, Set[int]] = {
            operator_id: set(operator.automatic_rest_days or [])
            for operator_id, operator in self._operator_cache.items()
        }
        self._manual_rest_index = self._build_manual_rest_index()
        self._history_start_date = min(
            calendar.start_date - timedelta(days=60),
            date(calendar.start_date.year, calendar.start_date.month, 1),
        )
        self._rest_counter_start_date = date(calendar.start_date.year, calendar.start_date.month, 1)
        self._rest_counter_end_date = calendar.end_date
        self._rest_usage: DefaultDict[OperatorId, Dict[Tuple[int, int], int]] = defaultdict(lambda: defaultdict(int))
        self._registered_rest_days: DefaultDict[OperatorId, Set[date]] = defaultdict(set)
        self._work_streak: DefaultDict[OperatorId, int] = defaultdict(int)
        self._rest_history = self._load_rest_history()
        self._assignment_history = self._load_assignment_history()
        self._operator_last_shift_type = self._load_operator_last_shift()
        self._operator_current_shift: Dict[OperatorId, Optional[str]] = {}
        self._operator_pending_shift: Dict[OperatorId, Optional[str]] = {}
        self._initialize_rest_tracking()
        self._initialize_shift_tracking()
        self._snapshot_rest_state()
        self._snapshot_shift_state()
        self._position_history = self._load_position_history()
        self._candidate_priority = self._build_candidate_priority()

    def generate(self, *, commit: bool = False) -> List[AssignmentDecision]:
        self._validate_calendar_range()
        self._restore_rest_state()
        self._restore_shift_state()
        self._post_shift_rest_by_assignment.clear()

        decisions = self._plan_schedule()
        self._enforce_daily_operator_uniqueness(decisions)

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

    def _build_operator_shift_catalog(self) -> Dict[OperatorId, Set[str]]:
        catalog: Dict[OperatorId, Set[str]] = {}
        for operator_id, operator in self._operator_cache.items():
            shift_types: Set[str] = set()
            for suggested in operator.suggested_positions.all():
                if suggested.category_id:
                    shift_types.add(suggested.category.shift_type)
            if shift_types:
                catalog[operator_id] = shift_types
        return catalog

    def _load_operator_last_shift(self) -> Dict[OperatorId, str]:
        if not self._operator_cache:
            return {}

        history_end = self.calendar.start_date - timedelta(days=1)
        if history_end < self._history_start_date:
            return {}

        operator_ids = list(self._operator_cache.keys())
        assignment_qs = (
            ShiftAssignment.objects.filter(
                operator_id__in=operator_ids,
                date__gte=self._history_start_date,
                date__lte=history_end,
            )
            .select_related("position__category")
            .order_by("operator_id", "-date", "-updated_at")
        )

        last_shift: Dict[OperatorId, Tuple[date, Optional[datetime], str]] = {}

        for assignment in assignment_qs:
            operator_id = assignment.operator_id
            if not operator_id:
                continue

            position = assignment.position
            if position is None or position.category_id is None:
                continue

            shift_type = position.category.shift_type
            recorded = last_shift.get(operator_id)
            if recorded:
                stored_date, stored_updated, _ = recorded
                if assignment.date < stored_date:
                    continue
                if (
                    assignment.date == stored_date
                    and stored_updated
                    and assignment.updated_at
                    and assignment.updated_at <= stored_updated
                ):
                    continue

            last_shift[operator_id] = (assignment.date, assignment.updated_at, shift_type)

        return {operator_id: shift for operator_id, (_, __, shift) in last_shift.items()}

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

    def _load_rest_history(self) -> Dict[OperatorId, Set[date]]:
        if not self._operator_cache:
            return {}

        operator_ids = list(self._operator_cache.keys())
        rest_qs = (
            OperatorRestPeriod.objects.filter(
                operator_id__in=operator_ids,
                start_date__lte=self.calendar.end_date,
                end_date__gte=self._history_start_date,
            )
            .exclude(status=RestPeriodStatus.CANCELLED)
        )

        rest_history: DefaultDict[OperatorId, Set[date]] = defaultdict(set)
        for period in rest_qs:
            operator_id = period.operator_id
            if not operator_id:
                continue
            start = max(period.start_date, self._history_start_date)
            end = min(period.end_date, self.calendar.end_date)
            for rest_day in self._daterange(start, end):
                rest_history[operator_id].add(rest_day)

        return dict(rest_history)

    def _load_assignment_history(self) -> Dict[OperatorId, Set[date]]:
        if not self._operator_cache:
            return {}

        history_end = self.calendar.start_date - timedelta(days=1)
        if history_end < self._history_start_date:
            return {}

        operator_ids = list(self._operator_cache.keys())
        assignment_qs = ShiftAssignment.objects.filter(
            operator_id__in=operator_ids,
            date__gte=self._history_start_date,
            date__lte=history_end,
        )

        assignment_history: DefaultDict[OperatorId, Set[date]] = defaultdict(set)
        for assignment in assignment_qs:
            operator_id = assignment.operator_id
            if not operator_id:
                continue
            assignment_history[operator_id].add(assignment.date)

        return dict(assignment_history)

    def _initialize_rest_tracking(self) -> None:
        for operator_id in self._operator_cache.keys():
            rest_days = self._rest_history.get(operator_id, set())
            for rest_day in sorted(rest_days):
                self._record_rest_day(operator_id, rest_day, initial=True)

            assignments = self._assignment_history.get(operator_id, set())
            streak = 0
            day = self.calendar.start_date - timedelta(days=1)
            while day >= self._history_start_date:
                if day in rest_days:
                    break
                if day not in assignments:
                    break
                streak += 1
                day -= timedelta(days=1)
            self._work_streak[operator_id] = streak

    def _initialize_shift_tracking(self) -> None:
        for operator_id in self._operator_cache.keys():
            last_shift = self._operator_last_shift_type.get(operator_id)
            if self._work_streak.get(operator_id, 0) > 0 and last_shift:
                self._operator_current_shift[operator_id] = last_shift
                self._operator_pending_shift[operator_id] = None
                continue

            self._operator_current_shift[operator_id] = None
            desired_shift = self._determine_post_rest_shift(operator_id, last_shift)
            self._operator_pending_shift[operator_id] = desired_shift

    def _determine_post_rest_shift(
        self,
        operator_id: OperatorId,
        last_shift: Optional[str],
    ) -> Optional[str]:
        if not last_shift or last_shift not in (ShiftType.DAY, ShiftType.NIGHT):
            return None

        opposite = ShiftType.NIGHT if last_shift == ShiftType.DAY else ShiftType.DAY
        if self._operator_supports_shift(operator_id, opposite):
            return opposite

        if self._operator_supports_shift(operator_id, last_shift):
            return last_shift

        return None

    def _operator_supports_shift(self, operator_id: OperatorId, shift_type: str) -> bool:
        catalog = self._operator_shift_catalog.get(operator_id, set())
        if not catalog:
            return False

        if shift_type == ShiftType.DAY:
            return ShiftType.DAY in catalog or ShiftType.MIXED in catalog
        if shift_type == ShiftType.NIGHT:
            return ShiftType.NIGHT in catalog or ShiftType.MIXED in catalog
        return shift_type in catalog

    def _snapshot_rest_state(self) -> None:
        self._rest_usage_snapshot: Dict[OperatorId, Dict[Tuple[int, int], int]] = {
            operator_id: dict(month_counts)
            for operator_id, month_counts in self._rest_usage.items()
        }
        self._registered_rest_days_snapshot: Dict[OperatorId, Set[date]] = {
            operator_id: set(days)
            for operator_id, days in self._registered_rest_days.items()
        }
        self._work_streak_snapshot: Dict[OperatorId, int] = dict(self._work_streak)

    def _snapshot_shift_state(self) -> None:
        self._operator_last_shift_type_snapshot: Dict[OperatorId, Optional[str]] = dict(
            self._operator_last_shift_type
        )
        self._operator_current_shift_snapshot: Dict[OperatorId, Optional[str]] = dict(
            self._operator_current_shift
        )
        self._operator_pending_shift_snapshot: Dict[OperatorId, Optional[str]] = dict(
            self._operator_pending_shift
        )

    def _restore_rest_state(self) -> None:
        self._rest_usage = defaultdict(lambda: defaultdict(int))
        for operator_id, month_counts in self._rest_usage_snapshot.items():
            month_usage = self._rest_usage[operator_id]
            for month_key, value in month_counts.items():
                month_usage[month_key] = value

        self._registered_rest_days = defaultdict(set)
        for operator_id, days in self._registered_rest_days_snapshot.items():
            self._registered_rest_days[operator_id].update(days)

        self._work_streak = defaultdict(int)
        for operator_id, streak in self._work_streak_snapshot.items():
            self._work_streak[operator_id] = streak

        self._planned_rest_days = defaultdict(set)

    def _restore_shift_state(self) -> None:
        self._operator_last_shift_type = dict(self._operator_last_shift_type_snapshot)
        self._operator_current_shift = dict(self._operator_current_shift_snapshot)
        self._operator_pending_shift = dict(self._operator_pending_shift_snapshot)

    @staticmethod
    def _month_key(target_date: date) -> Tuple[int, int]:
        return (target_date.year, target_date.month)

    def _record_rest_day(self, operator_id: OperatorId, rest_day: date, *, initial: bool) -> None:
        if rest_day in self._registered_rest_days[operator_id]:
            return

        self._registered_rest_days[operator_id].add(rest_day)

        if self._rest_counter_start_date <= rest_day <= self._rest_counter_end_date:
            month_key = self._month_key(rest_day)
            self._rest_usage[operator_id][month_key] += 1

        if not initial and self.calendar.start_date <= rest_day <= self.calendar.end_date:
            self._planned_rest_days[operator_id].add(rest_day)

    def _reserve_rest_day(
        self,
        *,
        operator_id: OperatorId,
        rest_day: date,
        rest_quota: int,
        dynamic_rest_blocks: DefaultDict[OperatorId, Set[date]],
    ) -> bool:
        if rest_day < self.calendar.start_date or rest_day > self.calendar.end_date:
            return False

        if rest_day in self._registered_rest_days[operator_id]:
            dynamic_rest_blocks[operator_id].add(rest_day)
            self._work_streak[operator_id] = 0
            self._handle_rest_day(operator_id)
            return True

        if rest_quota and rest_quota > 0:
            month_key = self._month_key(rest_day)
            if self._rest_usage[operator_id][month_key] >= rest_quota:
                return False

        self._record_rest_day(operator_id, rest_day, initial=False)
        dynamic_rest_blocks[operator_id].add(rest_day)
        self._work_streak[operator_id] = 0
        self._handle_rest_day(operator_id)
        return True

    def _force_rest_day(
        self,
        *,
        operator_id: OperatorId,
        rest_day: date,
        dynamic_rest_blocks: DefaultDict[OperatorId, Set[date]],
    ) -> None:
        if rest_day < self.calendar.start_date or rest_day > self.calendar.end_date:
            return

        if rest_day not in self._registered_rest_days[operator_id]:
            self._record_rest_day(operator_id, rest_day, initial=False)

        dynamic_rest_blocks[operator_id].add(rest_day)

    def _handle_rest_day(self, operator_id: OperatorId) -> None:
        current_shift = self._operator_current_shift.get(operator_id)
        if current_shift is not None:
            self._operator_last_shift_type[operator_id] = current_shift

        last_shift = current_shift or self._operator_last_shift_type.get(operator_id)
        self._operator_current_shift[operator_id] = None
        desired_shift = self._determine_post_rest_shift(operator_id, last_shift)
        self._operator_pending_shift[operator_id] = desired_shift

    def _prepare_day_state(
        self,
        current_date: date,
        dynamic_rest_blocks: DefaultDict[OperatorId, Set[date]],
    ) -> None:
        for operator_id in self._operator_cache.keys():
            registered_days = self._registered_rest_days.get(operator_id, set())
            if current_date in registered_days:
                self._handle_rest_day(operator_id)
                self._work_streak[operator_id] = 0
                continue

            dynamic_days = dynamic_rest_blocks.get(operator_id, set())
            if current_date in dynamic_days:
                if current_date not in registered_days:
                    self._record_rest_day(operator_id, current_date, initial=False)
                    registered_days = self._registered_rest_days.get(operator_id, set())
                self._handle_rest_day(operator_id)
                self._work_streak[operator_id] = 0

    def _unregister_planned_rest_day(self, operator_id: OperatorId, rest_day: date) -> None:
        planned_days = self._planned_rest_days.get(operator_id)
        if not planned_days or rest_day not in planned_days:
            return

        planned_days.discard(rest_day)

        registered_days = self._registered_rest_days.get(operator_id)
        if registered_days and rest_day in registered_days:
            registered_days.discard(rest_day)

        if self._rest_counter_start_date <= rest_day <= self._rest_counter_end_date:
            month_key = self._month_key(rest_day)
            month_usage = self._rest_usage.get(operator_id)
            if month_usage and month_key in month_usage:
                month_usage[month_key] -= 1
                if month_usage[month_key] <= 0:
                    month_usage.pop(month_key, None)

    def _should_block_for_rest(
        self,
        *,
        operator_id: OperatorId,
        position: PositionDefinition,
        target_date: date,
        dynamic_rest_blocks: DefaultDict[OperatorId, Set[date]],
    ) -> bool:
        category = position.category
        rest_quota = getattr(category, "rest_monthly_days", 0) or 0
        rest_max = getattr(category, "rest_max_consecutive_days", 0) or 0

        streak_value = self._work_streak.get(operator_id, 0)
        if rest_max and streak_value >= rest_max:
            if self._reserve_rest_day(
                operator_id=operator_id,
                rest_day=target_date,
                rest_quota=rest_quota,
                dynamic_rest_blocks=dynamic_rest_blocks,
            ):
                return True

        automatic_days = self._automatic_rest_index.get(operator_id)
        if automatic_days and target_date.weekday() in automatic_days:
            if self._reserve_rest_day(
                operator_id=operator_id,
                rest_day=target_date,
                rest_quota=rest_quota,
                dynamic_rest_blocks=dynamic_rest_blocks,
            ):
                return True

        return False


    def _register_assignment_shift(
        self,
        *,
        operator: UserProfile,
        position: PositionDefinition,
    ) -> None:
        operator_id = operator.id
        if operator_id is None:
            return

        shift_type = position.shift_type
        self._operator_last_shift_type[operator_id] = shift_type

        if shift_type in (ShiftType.DAY, ShiftType.NIGHT):
            self._operator_current_shift[operator_id] = shift_type
        else:
            self._operator_current_shift[operator_id] = None

        self._operator_pending_shift[operator_id] = None

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

    def _plan_schedule(self) -> List[AssignmentDecision]:
        decisions: List[AssignmentDecision] = []
        assigned_per_day: DefaultDict[date, Set[OperatorId]] = defaultdict(set)
        dynamic_rest_blocks: DefaultDict[OperatorId, Set[date]] = defaultdict(set)
        position_last_operator: Dict[PositionId, OperatorId] = {}

        for current_date in self._calendar_dates:
            day_decisions = self._schedule_day(
                current_date=current_date,
                assigned_per_day=assigned_per_day,
                dynamic_rest_blocks=dynamic_rest_blocks,
                position_last_operator=position_last_operator,
            )
            decisions.extend(day_decisions)

        return decisions

    def _enforce_daily_operator_uniqueness(self, decisions: List[AssignmentDecision]) -> None:
        seen: Dict[Tuple[date, OperatorId], int] = {}

        for index, decision in enumerate(decisions):
            operator = decision.operator
            if not operator or operator.id is None:
                continue

            key = (decision.date, operator.id)
            existing_index = seen.get(key)
            if existing_index is None:
                seen[key] = index
                continue

            previous_decision = decisions[existing_index]
            operator_name = operator.get_full_name() or operator.nombres or operator.apellidos or str(operator.id)
            conflict_note = (
                f"{operator_name} ya tenía turno asignado en {previous_decision.position.code} "
                f"para {decision.date.isoformat()}."
            )

            decisions[index] = AssignmentDecision(
                position=decision.position,
                operator=None,
                date=decision.date,
                alert_level=AssignmentAlertLevel.CRITICAL,
                notes=conflict_note,
            )

            position_id = getattr(decision.position, "id", None)
            if position_id is None:
                continue

            rest_key = (operator.id, decision.date, position_id)
            rest_days = self._post_shift_rest_by_assignment.pop(rest_key, set())
            if not rest_days:
                continue

            for rest_day in rest_days:
                self._unregister_planned_rest_day(operator.id, rest_day)

    def _schedule_day(
        self,
        *,
        current_date: date,
        assigned_per_day: DefaultDict[date, Set[OperatorId]],
        dynamic_rest_blocks: DefaultDict[OperatorId, Set[date]],
        position_last_operator: Dict[PositionId, OperatorId],
    ) -> List[AssignmentDecision]:
        self._prepare_day_state(current_date, dynamic_rest_blocks)

        active_positions = [
            position
            for position in self._positions
            if position.id is not None and position.is_active_on(current_date)
        ]
        if not active_positions:
            return []

        assigned_selection: List[Optional[UserProfile]] = [None] * len(active_positions)
        operator_to_position: Dict[OperatorId, int] = {}
        preferred_operator_per_position: Dict[int, Optional[OperatorId]] = {}

        def try_assign(position_index: int, seen_positions: Set[int], seen_operators: Set[OperatorId]) -> bool:
            if position_index in seen_positions:
                return False
            seen_positions.add(position_index)

            position = active_positions[position_index]
            candidate_order = self._candidate_order(position, position_last_operator)
            if position_index not in preferred_operator_per_position:
                preferred_operator_per_position[position_index] = candidate_order[0] if candidate_order else None

            for operator_id in candidate_order:
                operator = self._operator_cache.get(operator_id)
                if not operator:
                    continue

                if operator_id in seen_operators:
                    continue
                seen_operators.add(operator_id)

                if not self._is_operator_allowed_for_day(
                    operator=operator,
                    position=position,
                    target_date=current_date,
                    dynamic_rest_blocks=dynamic_rest_blocks,
                ):
                    continue

                current_owner = operator_to_position.get(operator_id)
                if current_owner is not None and current_owner != position_index:
                    previous_operator = assigned_selection[current_owner]
                    operator_to_position.pop(operator_id, None)
                    assigned_selection[current_owner] = None
                    if not try_assign(current_owner, seen_positions, seen_operators):
                        operator_to_position[operator_id] = current_owner
                        assigned_selection[current_owner] = previous_operator
                        continue

                operator_to_position[operator_id] = position_index
                assigned_selection[position_index] = operator
                return True

            return False

        for index in range(len(active_positions)):
            try_assign(index, set(), set())

        decisions: List[AssignmentDecision] = []

        day_assigned_set = assigned_per_day[current_date]
        day_assigned_set.clear()

        for position_index, (position, operator) in enumerate(zip(active_positions, assigned_selection)):
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
            day_assigned_set.add(operator.id)
            if position.id is not None:
                preferred_id = preferred_operator_per_position.get(position_index)
                if preferred_id is None or preferred_id == operator.id:
                    position_last_operator[position.id] = operator.id
            self._register_assignment_shift(
                operator=operator,
                position=position,
            )
            self._work_streak[operator.id] += 1
            self._schedule_post_shift_rest(
                operator_id=operator.id,
                position=position,
                work_date=current_date,
                dynamic_rest_blocks=dynamic_rest_blocks,
            )

        return decisions

    def _candidate_order(
        self,
        position: PositionDefinition,
        position_last_operator: Dict[PositionId, OperatorId],
    ) -> List[OperatorId]:
        position_id = position.id
        if position_id is None:
            return []

        candidate_order: List[OperatorId] = []
        last_operator_id = position_last_operator.get(position_id)
        if last_operator_id:
            candidate_order.append(last_operator_id)

        for operator_id in self._candidate_priority.get(position_id, []):
            if operator_id not in candidate_order:
                candidate_order.append(operator_id)

        return candidate_order

    def _is_shift_assignment_allowed(
        self,
        *,
        operator_id: OperatorId,
        position: PositionDefinition,
    ) -> bool:
        shift_type = position.shift_type
        current_shift = self._operator_current_shift.get(operator_id)
        if current_shift and not self._shift_types_compatible(shift_type, current_shift):
            return False

        desired_shift = self._operator_pending_shift.get(operator_id)
        if desired_shift and not self._shift_types_compatible(shift_type, desired_shift):
            if current_shift is None or not self._shift_types_compatible(current_shift, desired_shift):
                return False

        return True

    @staticmethod
    def _shift_types_compatible(
        lhs: Optional[str],
        rhs: Optional[str],
    ) -> bool:
        if lhs is None or rhs is None:
            return True
        if lhs == rhs:
            return True
        return lhs == ShiftType.MIXED or rhs == ShiftType.MIXED

    def _is_operator_allowed_for_day(
        self,
        *,
        operator: UserProfile,
        position: PositionDefinition,
        target_date: date,
        dynamic_rest_blocks: DefaultDict[OperatorId, Set[date]],
    ) -> bool:
        operator_id = operator.id
        if operator_id is None:
            return False

        if not operator.is_active:
            return False

        if not operator.is_active_on(target_date):
            return False

        if not self._is_shift_assignment_allowed(operator_id=operator_id, position=position):
            return False

        if target_date in self._manual_rest_index.get(operator_id, set()):
            return False

        if target_date in dynamic_rest_blocks.get(operator_id, set()):
            return False

        if self._should_block_for_rest(
            operator_id=operator_id,
            position=position,
            target_date=target_date,
            dynamic_rest_blocks=dynamic_rest_blocks,
        ):
            return False

        return True

    def _select_operator(
        self,
        *,
        position: PositionDefinition,
        target_date: date,
        assigned_per_day: DefaultDict[date, Set[OperatorId]],
        dynamic_rest_blocks: DefaultDict[OperatorId, Set[date]],
        position_last_operator: Dict[PositionId, OperatorId],
    ) -> Optional[UserProfile]:
        candidate_order = self._candidate_order(position, position_last_operator)

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

        if not self._is_shift_assignment_allowed(operator_id=operator_id, position=position):
            return False

        if operator_id in assigned_per_day.get(target_date, set()):
            return False

        if target_date in self._manual_rest_index.get(operator_id, set()):
            return False

        if target_date in dynamic_rest_blocks.get(operator_id, set()):
            return False

        if self._should_block_for_rest(
            operator_id=operator_id,
            position=position,
            target_date=target_date,
            dynamic_rest_blocks=dynamic_rest_blocks,
        ):
            return False

        return True

    def _schedule_post_shift_rest(
        self,
        *,
        operator_id: OperatorId,
        position: PositionDefinition,
        work_date: date,
        dynamic_rest_blocks: DefaultDict[OperatorId, Set[date]],
    ) -> None:
        category = position.category
        rest_span = getattr(category, "rest_post_shift_days", 0) or 0
        if rest_span <= 0:
            return

        position_id = getattr(position, "id", None)

        for offset in range(1, rest_span + 1):
            target_day = work_date + timedelta(days=offset)
            if target_day < self.calendar.start_date or target_day > self.calendar.end_date:
                continue
            self._force_rest_day(
                operator_id=operator_id,
                rest_day=target_day,
                dynamic_rest_blocks=dynamic_rest_blocks,
            )
            if position_id is not None:
                rest_key = (operator_id, work_date, position_id)
                self._post_shift_rest_by_assignment[rest_key].add(target_day)

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

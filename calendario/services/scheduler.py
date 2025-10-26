from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

from django.db import transaction
from django.db.models import Q

from ..models import (
    AssignmentAlertLevel,
    AssignmentDecision,
    OverloadPolicyData,
    PositionCategory,
    PositionDefinition,
    ShiftAssignment,
    ShiftCalendar,
    ShiftType,
    WorkloadSnapshot,
    filter_capabilities_for_category,
    required_skill_for_complexity,
    resolve_overload_policy,
)
from ..models import OperatorCapability
from users.models import UserProfile


@dataclass(slots=True)
class SchedulerOptions:
    honor_rest_rules: bool = True
    honor_preferences: bool = True
    allow_lower_complexity: bool = True
    replace_existing: bool = True


@dataclass
class OperatorState:
    operator: UserProfile
    assigned_dates: set[date] = field(default_factory=set)
    total_assignments: int = 0
    consecutive_streak: int = 0
    consecutive_day_streak: int = 0
    consecutive_night_streak: int = 0
    last_assignment_date: Optional[date] = None
    last_shift_type: Optional[str] = None
    blocked_until: Optional[date] = None
    overtime_streak: int = 0

    def register_assignment(self, target_date: date, shift_type: str, is_overtime: bool) -> None:
        if self.last_assignment_date and target_date == self.last_assignment_date + timedelta(days=1):
            self.consecutive_streak += 1
        else:
            self.consecutive_streak = 1

        if shift_type == ShiftType.NIGHT:
            if self.last_shift_type == ShiftType.NIGHT and self.last_assignment_date and target_date == self.last_assignment_date + timedelta(days=1):
                self.consecutive_night_streak += 1
            else:
                self.consecutive_night_streak = 1
            self.consecutive_day_streak = 0
        else:
            if self.last_shift_type == ShiftType.DAY and self.last_assignment_date and target_date == self.last_assignment_date + timedelta(days=1):
                self.consecutive_day_streak += 1
            else:
                self.consecutive_day_streak = 1
            self.consecutive_night_streak = 0

        self.last_assignment_date = target_date
        self.last_shift_type = shift_type
        self.assigned_dates.add(target_date)
        self.total_assignments += 1
        self.overtime_streak = self.overtime_streak + 1 if is_overtime else 0


class CalendarScheduler:
    def __init__(self, calendar: ShiftCalendar, *, options: Optional[SchedulerOptions] = None) -> None:
        if calendar.start_date > calendar.end_date:
            raise ValueError("El calendario tiene un rango inválido.")
        self.calendar = calendar
        self.options = options or SchedulerOptions()
        self._operator_states: Dict[int, OperatorState] = {}
        self._operator_capabilities: Dict[int, List[OperatorCapability]] = {}
        self._capabilities_by_category: Dict[int, List[OperatorCapability]] = defaultdict(list)
        self._preferred_farms: Dict[int, Optional[int]] = {}
        self._overload_policies: Dict[int, OverloadPolicyData] = {}
        self._positions: List[PositionDefinition] = []
        self._calendar_dates = list(self._daterange(calendar.start_date, calendar.end_date))
        self._load_context()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def generate(self, *, commit: bool = False) -> List[AssignmentDecision]:
        decisions: List[AssignmentDecision] = []

        for current_date in self._calendar_dates:
            active_positions = [
                position for position in self._positions if position.is_active_on(current_date)
            ]

            for position in active_positions:
                decision = self._assign_for_position(position, current_date)
                decisions.append(decision)

        if commit:
            self._apply_decisions(decisions)

        return decisions

    # ------------------------------------------------------------------
    # Context loading helpers
    # ------------------------------------------------------------------
    def _load_context(self) -> None:
        self._positions = list(
            PositionDefinition.objects.select_related("farm", "chicken_house", "category")
            .prefetch_related("rooms")
            .filter(valid_from__lte=self.calendar.end_date)
            .filter(Q(valid_until__isnull=True) | Q(valid_until__gte=self.calendar.start_date))
            .order_by("display_order", "id")
        )

        relevant_categories: set[int] = set()
        for position in self._positions:
            if position.category_id:
                relevant_categories.add(position.category_id)

        capabilities = list(
            OperatorCapability.objects.select_related("operator", "operator__preferred_farm")
        )

        if not capabilities and relevant_categories:
            capabilities = self._build_default_capabilities(relevant_categories)

        for capability in capabilities:
            operator_id = capability.operator_id
            self._operator_capabilities.setdefault(operator_id, []).append(capability)
            self._capabilities_by_category[capability.category_id].append(capability)
            if operator_id not in self._operator_states:
                self._operator_states[operator_id] = OperatorState(operator=capability.operator)
                self._preferred_farms[operator_id] = capability.operator.preferred_farm_id
            else:
                self._preferred_farms[operator_id] = capability.operator.preferred_farm_id

    def _build_default_capabilities(self, categories: set[int]) -> List[OperatorCapability]:
        """Create in-memory capabilities for all active operators when no rules exist."""
        if not categories:
            return []

        default_skill = OperatorCapability._meta.get_field("skill_score").get_default()

        operators = (
            UserProfile.objects.filter(is_active=True)
            .select_related("preferred_farm")
            .prefetch_related("roles")
        )

        fallback_capabilities: List[OperatorCapability] = []
        for operator in operators:
            for category in categories:
                fallback_capabilities.append(
                    OperatorCapability(
                        operator=operator,
                        category_id=category,
                        skill_score=default_skill,
                    )
                )

        return fallback_capabilities

    def _get_overload_policy(self, category: PositionCategory) -> OverloadPolicyData:
        policy = self._overload_policies.get(category.id)
        if policy:
            return policy
        policy = resolve_overload_policy(category)
        self._overload_policies[category.id] = policy
        return policy

    # ------------------------------------------------------------------
    # Assignment core
    # ------------------------------------------------------------------
    def _assign_for_position(self, position: PositionDefinition, current_date: date) -> AssignmentDecision:
        candidates = filter_capabilities_for_category(
            self._capabilities_by_category.get(position.category_id, []),
            position.category_id,
        )

        ranked_candidates = sorted(
            candidates,
            key=lambda capability: (
                self._preference_sort_key(capability.operator_id, position.farm_id),
                -capability.skill_score,
                self._operator_states[capability.operator_id].total_assignments,
            ),
        )

        for capability in ranked_candidates:
            state = self._operator_states[capability.operator_id]

            if not self._is_operator_available(state, capability, position, current_date):
                continue

            alert_level, is_overtime, notes, overtime_points = self._evaluate_assignment_risk(
                state, capability, position, current_date
            )

            self._finalize_state(state, capability, position, current_date, is_overtime)

            return AssignmentDecision(
                position=position,
                operator=capability.operator,
                date=current_date,
                alert_level=alert_level,
                is_overtime=is_overtime,
                notes=notes,
                overtime_points=overtime_points,
            )

        # No matching operator: mark as critical gap.
        return AssignmentDecision(
            position=position,
            operator=None,
            date=current_date,
            alert_level=AssignmentAlertLevel.CRITICAL,
            is_overtime=False,
            notes="Sin operario elegible",
        )

    def _preference_sort_key(self, operator_id: int, farm_id: int) -> Tuple[int, int]:
        if not self.options.honor_preferences:
            return (1, operator_id)

        preferred_farm_id = self._preferred_farms.get(operator_id)
        if not preferred_farm_id:
            return (1, operator_id)

        if preferred_farm_id == farm_id:
            return (0, operator_id)

        return (2, operator_id)

    def _is_operator_available(
        self,
        state: OperatorState,
        capability: OperatorCapability,
        position: PositionDefinition,
        current_date: date,
    ) -> bool:
        if current_date in state.assigned_dates:
            return False

        if state.blocked_until and current_date <= state.blocked_until:
            return False

        required_skill = required_skill_for_complexity(position.complexity)
        operator_skill = capability.skill_score

        if operator_skill < required_skill and not (
            self.options.allow_lower_complexity and position.allow_lower_complexity
        ):
            return False

        if not self.options.honor_rest_rules:
            return True

        rest_settings = position.category
        new_streak = self._calculate_new_streak(state, current_date)
        allowed_max = rest_settings.rest_max_consecutive_days
        if new_streak > allowed_max:
            policy = self._get_overload_policy(position.category)
            extended_limit = allowed_max + policy.extra_day_limit
            if new_streak > extended_limit:
                return False

        return True

    def _evaluate_assignment_risk(
        self,
        state: OperatorState,
        capability: OperatorCapability,
        position: PositionDefinition,
        current_date: date,
    ) -> Tuple[AssignmentAlertLevel, bool, str, int]:
        alert_level = AssignmentAlertLevel.NONE
        is_overtime = False
        notes = ""
        overtime_points = 0

        required_skill = required_skill_for_complexity(position.complexity)
        operator_skill = capability.skill_score
        if operator_skill < required_skill:
            difference = required_skill - operator_skill
            alert_level = AssignmentAlertLevel.WARN if difference == 1 else AssignmentAlertLevel.CRITICAL
            notes = "Cobertura con operario de menor habilidad"

        rest_settings = position.category
        new_streak = self._calculate_new_streak(state, current_date)
        if new_streak > rest_settings.rest_max_consecutive_days:
            policy = self._get_overload_policy(position.category)
            is_overtime = True
            alert_level = max(alert_level, policy.alert_level, key=self._alert_priority)
            overtime_points = policy.overtime_points
            notes = "Sobrecarga autorizada" if not notes else f"{notes}; sobrecarga"

        if state.last_shift_type == ShiftType.NIGHT and position.shift_type == ShiftType.NIGHT:
            # Incentivar rotación posterior a turnos nocturnos.
            notes = notes or "Revisar rotación posterior a nocturnos"
            alert_level = max(alert_level, AssignmentAlertLevel.WARN, key=self._alert_priority)

        return alert_level, is_overtime, notes, overtime_points

    def _finalize_state(
        self,
        state: OperatorState,
        capability: OperatorCapability,
        position: PositionDefinition,
        current_date: date,
        is_overtime: bool,
    ) -> None:
        state.register_assignment(current_date, position.shift_type, is_overtime)

        if position.shift_type == ShiftType.NIGHT:
            post_shift_days = position.category.rest_post_shift_days
            if post_shift_days:
                state.blocked_until = current_date + timedelta(days=post_shift_days)

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------
    def _apply_decisions(self, decisions: Iterable[AssignmentDecision]) -> None:
        with transaction.atomic():
            if self.options.replace_existing:
                self.calendar.assignments.filter(is_auto_assigned=True).delete()

            assignments_to_create: List[ShiftAssignment] = []
            for decision in decisions:
                if not decision.operator:
                    continue

                assignments_to_create.append(
                    ShiftAssignment(
                        calendar=self.calendar,
                        position=decision.position,
                        date=decision.date,
                        operator=decision.operator,
                        is_auto_assigned=True,
                        alert_level=decision.alert_level,
                        is_overtime=decision.is_overtime,
                        overtime_points=decision.overtime_points,
                        notes=decision.notes,
                    )
                )

            if assignments_to_create:
                ShiftAssignment.objects.bulk_create(assignments_to_create, batch_size=100)

            self._rebuild_workload_snapshots()

    def _rebuild_workload_snapshots(self) -> None:
        self.calendar.workload_snapshots.all().delete()

        aggregates: Dict[Tuple[int, date], Dict[str, int]] = defaultdict(lambda: defaultdict(int))

        assignments = (
            self.calendar.assignments.select_related("position", "operator")
            .order_by("operator_id", "date")
        )
        total_days_by_month: Dict[date, int] = defaultdict(int)
        for calendar_date in self._calendar_dates:
            month_key = calendar_date.replace(day=1)
            total_days_by_month[month_key] += 1

        for assignment in assignments:
            month_key = assignment.date.replace(day=1)
            slot = aggregates[(assignment.operator_id, month_key)]
            slot["total_shifts"] += 1
            if assignment.position.shift_type == ShiftType.NIGHT:
                slot["night_shifts"] += 1
            else:
                slot["day_shifts"] += 1
            if assignment.is_overtime:
                slot["overtime_days"] += 1
                slot["overtime_points"] += assignment.overtime_points

        snapshots: List[WorkloadSnapshot] = []
        for (operator_id, month_key), values in aggregates.items():
            rest_days = total_days_by_month.get(month_key, 0) - values.get("total_shifts", 0)
            snapshots.append(
                WorkloadSnapshot(
                    calendar=self.calendar,
                    operator_id=operator_id,
                    month_reference=month_key,
                    total_shifts=values.get("total_shifts", 0),
                    day_shifts=values.get("day_shifts", 0),
                    night_shifts=values.get("night_shifts", 0),
                    rest_days=max(rest_days, 0),
                    overtime_days=values.get("overtime_days", 0),
                    overtime_points_total=values.get("overtime_points", 0),
                )
            )

        if snapshots:
            WorkloadSnapshot.objects.bulk_create(snapshots, batch_size=100)

    @staticmethod
    def _calculate_new_streak(state: OperatorState, current_date: date) -> int:
        if state.last_assignment_date and current_date == state.last_assignment_date + timedelta(days=1):
            return state.consecutive_streak + 1
        return 1

    @staticmethod
    def _alert_priority(level: AssignmentAlertLevel) -> int:
        priorities = {
            AssignmentAlertLevel.NONE: 0,
            AssignmentAlertLevel.WARN: 1,
            AssignmentAlertLevel.CRITICAL: 2,
        }
        return priorities[level]

    @staticmethod
    def _daterange(start: date, end: date) -> Iterator[date]:
        current = start
        while current <= end:
            yield current
            current += timedelta(days=1)

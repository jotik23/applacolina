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
    OverloadAllowance,
    PositionCategory,
    PositionDefinition,
    RestRule,
    ShiftAssignment,
    ShiftCalendar,
    ShiftType,
    WorkloadSnapshot,
    filter_capabilities_for_category,
    required_skill_for_complexity,
)
from ..models import OperatorCapability
from users.models import Role, UserProfile


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
        self._capabilities_by_category: Dict[str, List[OperatorCapability]] = defaultdict(list)
        self._preferred_farms: Dict[int, Optional[int]] = {}
        self._rest_rules: Dict[Tuple[int, str], List[RestRule]] = defaultdict(list)
        self._overload_rules: Dict[int, List[OverloadAllowance]] = defaultdict(list)
        self._prefetched_roles: Dict[int, List[Role]] = {}
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
            PositionDefinition.objects.select_related("farm", "chicken_house")
            .prefetch_related("rooms")
            .filter(valid_from__lte=self.calendar.end_date)
            .filter(Q(valid_until__isnull=True) | Q(valid_until__gte=self.calendar.start_date))
            .order_by("display_order", "id")
        )

        capabilities = list(
            OperatorCapability.objects.select_related("operator", "operator__preferred_farm")
            .prefetch_related("operator__roles")
        )

        for capability in capabilities:
            operator_id = capability.operator_id
            self._operator_capabilities.setdefault(operator_id, []).append(capability)
            self._capabilities_by_category[capability.category].append(capability)
            if operator_id not in self._operator_states:
                self._operator_states[operator_id] = OperatorState(operator=capability.operator)
                self._prefetched_roles[operator_id] = list(capability.operator.roles.all())
                self._preferred_farms[operator_id] = capability.operator.preferred_farm_id
            else:
                self._preferred_farms[operator_id] = capability.operator.preferred_farm_id

        rest_rules = list(
            RestRule.objects.select_related("role")
            .filter(active_from__lte=self.calendar.end_date)
            .filter(Q(active_until__isnull=True) | Q(active_until__gte=self.calendar.start_date))
        )
        for rule in rest_rules:
            self._rest_rules[(rule.role_id, rule.shift_type)].append(rule)
        for rules in self._rest_rules.values():
            rules.sort(key=lambda item: item.active_from, reverse=True)

        overloads = list(
            OverloadAllowance.objects.select_related("role")
            .filter(active_from__lte=self.calendar.end_date)
            .filter(Q(active_until__isnull=True) | Q(active_until__gte=self.calendar.start_date))
        )
        for allowance in overloads:
            self._overload_rules[allowance.role_id].append(allowance)
        for rules in self._overload_rules.values():
            rules.sort(key=lambda item: item.active_from, reverse=True)

    # ------------------------------------------------------------------
    # Assignment core
    # ------------------------------------------------------------------
    def _assign_for_position(self, position: PositionDefinition, current_date: date) -> AssignmentDecision:
        candidates = filter_capabilities_for_category(
            self._capabilities_by_category.get(position.category, []),
            position.category,
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

            alert_level, is_overtime, notes = self._evaluate_assignment_risk(
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

        rest_rule = self._get_rest_rule_for_operator(capability.operator_id, position.shift_type, current_date)
        if rest_rule:
            new_streak = self._calculate_new_streak(state, current_date)
            allowed_max = rest_rule.max_consecutive_days

            if new_streak > allowed_max:
                overload_rule = self._get_overload_rule_for_operator(capability.operator_id, current_date)
                if not overload_rule:
                    return False

                extended_limit = allowed_max + overload_rule.max_consecutive_extra_days
                if new_streak > extended_limit:
                    return False

        return True

    def _evaluate_assignment_risk(
        self,
        state: OperatorState,
        capability: OperatorCapability,
        position: PositionDefinition,
        current_date: date,
    ) -> Tuple[AssignmentAlertLevel, bool, str]:
        alert_level = AssignmentAlertLevel.NONE
        is_overtime = False
        notes = ""

        required_skill = required_skill_for_complexity(position.complexity)
        operator_skill = capability.skill_score
        if operator_skill < required_skill:
            difference = required_skill - operator_skill
            alert_level = AssignmentAlertLevel.WARN if difference == 1 else AssignmentAlertLevel.CRITICAL
            notes = "Cobertura con operario de menor habilidad"

        rest_rule = self._get_rest_rule_for_operator(capability.operator_id, position.shift_type, current_date)
        if rest_rule:
            new_streak = self._calculate_new_streak(state, current_date)
            if new_streak > rest_rule.max_consecutive_days:
                overload_rule = self._get_overload_rule_for_operator(capability.operator_id, current_date)
                if overload_rule:
                    is_overtime = True
                    alert_level = max(alert_level, overload_rule.highlight_level, key=self._alert_priority)
                    notes = "Sobrecarga autorizada" if not notes else f"{notes}; sobrecarga"

        if state.last_shift_type == ShiftType.NIGHT and position.shift_type == ShiftType.NIGHT:
            # Incentivar rotación posterior a turnos nocturnos.
            notes = notes or "Revisar rotación posterior a nocturnos"
            alert_level = max(alert_level, AssignmentAlertLevel.WARN, key=self._alert_priority)

        return alert_level, is_overtime, notes

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
            rest_rule = self._get_rest_rule_for_operator(capability.operator_id, position.shift_type, current_date)
            if rest_rule and rest_rule.post_shift_rest_days:
                state.blocked_until = current_date + timedelta(days=rest_rule.post_shift_rest_days)

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
                )
            )

        if snapshots:
            WorkloadSnapshot.objects.bulk_create(snapshots, batch_size=100)

    # ------------------------------------------------------------------
    # Rule helpers
    # ------------------------------------------------------------------
    def _get_rest_rule_for_operator(
        self, operator_id: int, shift_type: str, target_date: date
    ) -> Optional[RestRule]:
        roles = self._prefetched_roles.get(operator_id, [])
        applicable_rules: List[RestRule] = []
        for role in roles:
            for rule in self._rest_rules.get((role.id, shift_type), []):
                if rule.active_from <= target_date and (
                    not rule.active_until or rule.active_until >= target_date
                ):
                    applicable_rules.append(rule)
        if not applicable_rules:
            return None
        return min(applicable_rules, key=lambda rule: rule.max_consecutive_days)

    def _get_overload_rule_for_operator(self, operator_id: int, target_date: date) -> Optional[OverloadAllowance]:
        roles = self._prefetched_roles.get(operator_id, [])
        applicable: List[OverloadAllowance] = []
        for role in roles:
            for rule in self._overload_rules.get(role.id, []):
                if rule.active_from <= target_date and (
                    not rule.active_until or rule.active_until >= target_date
                ):
                    applicable.append(rule)
        if not applicable:
            return None
        return min(applicable, key=lambda rule: rule.max_consecutive_extra_days)

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

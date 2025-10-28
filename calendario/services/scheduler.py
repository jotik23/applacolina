from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, Iterable, Iterator, List, Optional, Set, Tuple

from django.db import transaction
from django.db.models import Q

from ..models import (
    AssignmentAlertLevel,
    AssignmentDecision,
    OperatorRestPeriod,
    OverloadPolicyData,
    PositionCategory,
    PositionDefinition,
    RestPeriodSource,
    RestPeriodStatus,
    ShiftAssignment,
    ShiftCalendar,
    ShiftType,
    WorkloadSnapshot,
    filter_capabilities_for_category,
    complexity_score,
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
    rest_dates: set[date] = field(default_factory=set)
    manual_rest_dates: set[date] = field(default_factory=set)
    manual_rest_windows: List[Tuple[date, date]] = field(default_factory=list)
    manual_rest_weekly: Dict[Tuple[int, int], Dict[int, int]] = field(default_factory=dict)
    last_manual_rest_end: Optional[date] = None
    initial_total_assignments: int = 0
    calendar_assignment_count: int = 0
    last_rest_end: Optional[date] = None
    employment_start_date: Optional[date] = None
    employment_end_date: Optional[date] = None

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

        if target_date in self.rest_dates:
            self.rest_dates.discard(target_date)

    def is_rest_day(self, target_date: date) -> bool:
        return target_date in self.rest_dates


@dataclass
class PositionContinuity:
    operator_id: int
    last_date: date
    streak: int = 1


class CalendarScheduler:
    def __init__(self, calendar: ShiftCalendar, *, options: Optional[SchedulerOptions] = None) -> None:
        if calendar.start_date > calendar.end_date:
            raise ValueError("El calendario tiene un rango inválido.")
        self.calendar = calendar
        self.options = options or SchedulerOptions()
        self._operator_states: Dict[int, OperatorState] = {}
        self._operator_capabilities: Dict[int, List[OperatorCapability]] = {}
        self._capabilities_by_category: Dict[int, List[OperatorCapability]] = defaultdict(list)
        self._operator_suggestions: Dict[int, Set[int]] = {}
        self._preferred_farms: Dict[int, Optional[int]] = {}
        self._overload_policies: Dict[int, OverloadPolicyData] = {}
        self._rest_periods: Dict[int, List[OperatorRestPeriod]] = defaultdict(list)
        self._positions: List[PositionDefinition] = []
        self._category_rest_days: Dict[int, set[int]] = {}
        self._position_continuity: Dict[int, PositionContinuity] = {}
        self._calendar_dates = list(self._daterange(calendar.start_date, calendar.end_date))
        self._load_context()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def generate(self, *, commit: bool = False) -> List[AssignmentDecision]:
        decisions: List[AssignmentDecision] = []

        for current_date in self._calendar_dates:
            daily_decisions = self._assign_for_day(current_date)
            decisions.extend(daily_decisions)

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
            .filter(is_active=True)
            .filter(valid_from__lte=self.calendar.end_date)
            .filter(Q(valid_until__isnull=True) | Q(valid_until__gte=self.calendar.start_date))
            .order_by("display_order", "id")
        )
        available_position_ids: Set[int] = {position.id for position in self._positions}

        relevant_categories: set[int] = set()
        for position in self._positions:
            if position.category_id:
                relevant_categories.add(position.category_id)
                if position.category_id not in self._category_rest_days:
                    rest_days = position.category.automatic_rest_days or []
                    self._category_rest_days[position.category_id] = set(rest_days)

        capabilities = list(
            OperatorCapability.objects.select_related("operator", "operator__preferred_farm")
        )

        if not capabilities and relevant_categories:
            capabilities = self._build_default_capabilities(relevant_categories)

        operator_ids: Set[int] = set()
        for capability in capabilities:
            operator = capability.operator
            if not operator:
                continue
            if not operator.is_active:
                continue
            if operator.employment_end_date and operator.employment_end_date < self.calendar.start_date:
                continue
            if operator.employment_start_date and operator.employment_start_date > self.calendar.end_date:
                continue

            operator_id = capability.operator_id or operator.id
            if operator_id is None:
                continue
            capability.operator_id = operator_id
            self._operator_capabilities.setdefault(operator_id, []).append(capability)
            self._capabilities_by_category[capability.category_id].append(capability)
            operator_ids.add(operator_id)
            if operator_id not in self._operator_states:
                self._operator_states[operator_id] = OperatorState(
                    operator=operator,
                    employment_start_date=operator.employment_start_date,
                    employment_end_date=operator.employment_end_date,
                )
            self._preferred_farms[operator_id] = operator.preferred_farm_id

        self._operator_suggestions = self._load_operator_suggestions(operator_ids, available_position_ids)

        self._apply_rest_context()
        self._load_recent_history()

    def _load_operator_suggestions(
        self,
        operator_ids: Iterable[int],
        available_positions: Set[int],
    ) -> Dict[int, Set[int]]:
        normalized_ids = {int(operator_id) for operator_id in operator_ids if operator_id}
        if not normalized_ids:
            return {}

        through_model = UserProfile.suggested_positions.through
        suggestion_rows = through_model.objects.filter(userprofile_id__in=normalized_ids).values_list(
            "userprofile_id",
            "positiondefinition_id",
        )

        suggestions: Dict[int, Set[int]] = defaultdict(set)
        for operator_id, position_id in suggestion_rows:
            if available_positions and position_id not in available_positions:
                continue
            suggestions[int(operator_id)].add(int(position_id))

        return {operator_id: positions for operator_id, positions in suggestions.items()}

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
            if operator.employment_end_date and operator.employment_end_date < self.calendar.start_date:
                continue
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
    def _assign_for_day(self, current_date: date) -> List[AssignmentDecision]:
        active_positions: List[PositionDefinition] = []
        for position in self._positions:
            if not position.is_active_on(current_date):
                continue
            if self.options.honor_rest_rules and self._is_category_rest_day(position, current_date):
                if not self._can_generate_on_category_rest_day(position, current_date):
                    continue
            active_positions.append(position)

        if self.options.honor_rest_rules:
            for position in active_positions:
                continuity = self._position_continuity.get(position.id)
                if not continuity:
                    continue
                state = self._operator_states.get(continuity.operator_id)
                if not state:
                    continue
                if state.is_rest_day(current_date) or (state.blocked_until and current_date <= state.blocked_until):
                    self._position_continuity.pop(position.id, None)

        if not active_positions:
            return []

        candidate_map: Dict[int, List[OperatorCapability]] = {}
        for position in active_positions:
            candidate_map[position.id] = self._eligible_candidates(position, current_date)

        matches = self._maximize_daily_coverage(active_positions, candidate_map)

        decisions: List[AssignmentDecision] = []

        for position in active_positions:
            capability = matches.get(position.id)

            if not capability:
                self._position_continuity.pop(position.id, None)
                decisions.append(
                    AssignmentDecision(
                        position=position,
                        operator=None,
                        date=current_date,
                        alert_level=AssignmentAlertLevel.CRITICAL,
                        is_overtime=False,
                        notes="Sin operario elegible",
                    )
                )
                continue

            state = self._operator_states[capability.operator_id]
            alert_level, is_overtime, notes, overtime_points = self._evaluate_assignment_risk(
                state, capability, position, current_date
            )

            self._update_position_continuity(position.id, capability.operator_id, current_date)
            decisions.append(
                AssignmentDecision(
                    position=position,
                    operator=capability.operator,
                    date=current_date,
                    alert_level=alert_level,
                    is_overtime=is_overtime,
                    notes=notes,
                    overtime_points=overtime_points,
                )
            )
            self._finalize_state(state, capability, position, current_date, is_overtime)

        return decisions

    def _eligible_candidates(
        self,
        position: PositionDefinition,
        current_date: date,
    ) -> List[OperatorCapability]:
        raw_candidates = filter_capabilities_for_category(
            self._capabilities_by_category.get(position.category_id, []),
            position.category_id,
        )

        def _ranking_key(capability: OperatorCapability) -> Tuple[int, Tuple[int, ...]]:
            suggestion_flag = 0 if self._is_suggested_position(capability.operator_id, position.id) else 1
            return (suggestion_flag, self._candidate_sort_key(capability, position, current_date))

        ranked_candidates = sorted(raw_candidates, key=_ranking_key)

        eligible: List[OperatorCapability] = []
        for capability in ranked_candidates:
            state = self._operator_states[capability.operator_id]
            if self._is_operator_available(state, capability, position, current_date):
                eligible.append(capability)

        return eligible

    def _maximize_daily_coverage(
        self,
        positions: List[PositionDefinition],
        candidate_map: Dict[int, List[OperatorCapability]],
    ) -> Dict[int, OperatorCapability]:
        operator_matches: Dict[int, int] = {}
        position_matches: Dict[int, OperatorCapability] = {}

        def attempt_assignment(position_id: int, visited_ops: set[int]) -> bool:
            for capability in candidate_map.get(position_id, []):
                operator_id = capability.operator_id
                if operator_id in visited_ops:
                    continue
                visited_ops.add(operator_id)

                current_position = operator_matches.get(operator_id)
                if current_position is None or attempt_assignment(current_position, visited_ops):
                    operator_matches[operator_id] = position_id
                    position_matches[position_id] = capability
                    return True

            return False

        sorted_positions = sorted(
            positions,
            key=lambda position: (
                -complexity_score(position.complexity),
                len(candidate_map.get(position.id, [])),
                position.display_order,
                position.id,
            ),
        )

        for position in sorted_positions:
            attempt_assignment(position.id, set())

        return position_matches

    def _preference_sort_key(self, operator_id: int, farm_id: int) -> Tuple[int, int]:
        if not self.options.honor_preferences:
            return (1, operator_id)

        preferred_farm_id = self._preferred_farms.get(operator_id)
        if not preferred_farm_id:
            return (1, operator_id)

        if preferred_farm_id == farm_id:
            return (0, operator_id)

        return (2, operator_id)

    def _is_suggested_position(self, operator_id: int, position_id: int) -> bool:
        suggested = self._operator_suggestions.get(operator_id)
        if not suggested:
            return False
        return position_id in suggested

    def _candidate_sort_key(
        self,
        capability: OperatorCapability,
        position: PositionDefinition,
        current_date: date,
    ) -> Tuple[int, ...]:
        state = self._operator_states[capability.operator_id]
        preference_rank, preference_id = self._preference_sort_key(capability.operator_id, position.farm_id)
        continuity = self._position_continuity.get(position.id)
        continuity_priority = 2
        continuity_score = 0
        previous_day = current_date - timedelta(days=1)
        if continuity:
            if continuity.operator_id == capability.operator_id:
                if continuity.last_date == previous_day:
                    continuity_priority = 0
                    continuity_score = -continuity.streak
                else:
                    continuity_priority = 1
            else:
                continuity_priority = 4 if position.shift_type == ShiftType.NIGHT else 3
                if state.total_assignments == 0:
                    prior_rest = any(
                        self.calendar.start_date <= rest_day < current_date for rest_day in state.rest_dates
                    )
                    if prior_rest:
                        continuity_priority = 0
                        continuity_score = 0
                    else:
                        continuity_priority = min(continuity_priority, 1)
        suggestion_rank = 0 if self._is_suggested_position(capability.operator_id, position.id) else 1
        rest_flag, base_rest_value = self._rest_pressure(state, position, current_date)
        overtime_risk = 1 if self._would_trigger_overtime(state, position, current_date) else 0
        required_skill = required_skill_for_complexity(position.complexity)
        skill_gap = max(required_skill - capability.skill_score, 0)
        recent_assignments = sum(1 for day in state.assigned_dates if day >= self.calendar.start_date)

        weekend_priority = 1
        weekend_value = 0
        rest_value = base_rest_value
        if self._is_weekend(current_date):
            worked_previous_day = state.last_assignment_date == current_date - timedelta(days=1)
            weekend_priority = 0 if worked_previous_day else 1
            if worked_previous_day:
                weekend_value = -state.consecutive_streak
                if not rest_flag:
                    rest_value = -base_rest_value

        if suggestion_rank == 0 and continuity_priority > 0:
            continuity_priority = min(continuity_priority, 1)

        return (
            rest_flag,
            preference_rank,
            overtime_risk,
            continuity_priority,
            continuity_score,
            suggestion_rank,
            weekend_priority,
            weekend_value,
            rest_value,
            skill_gap,
            recent_assignments,
            base_rest_value,
            -capability.skill_score,
            preference_id,
        )

    def _update_position_continuity(self, position_id: int, operator_id: int, current_date: date) -> None:
        entry = self._position_continuity.get(position_id)
        previous_day = current_date - timedelta(days=1)
        if entry and entry.operator_id == operator_id and entry.last_date == previous_day:
            self._position_continuity[position_id] = PositionContinuity(
                operator_id=operator_id,
                last_date=current_date,
                streak=entry.streak + 1,
            )
        else:
            self._position_continuity[position_id] = PositionContinuity(
                operator_id=operator_id,
                last_date=current_date,
                streak=1,
            )

    def _rest_pressure(
        self,
        state: OperatorState,
        position: PositionDefinition,
        current_date: date,
    ) -> Tuple[int, int]:
        if not self.options.honor_rest_rules:
            return (0, state.consecutive_streak)

        rest_settings = position.category
        new_streak = self._calculate_new_streak(state, current_date)
        rest_min_frequency = rest_settings.rest_min_frequency or 0
        rest_min_consecutive = rest_settings.rest_min_consecutive_days or 0
        rest_max_limit = rest_settings.rest_max_consecutive_days or 0
        if rest_max_limit <= 0 and rest_min_frequency <= 0:
            return (0, new_streak)

        policy = None
        if rest_max_limit > 0 or rest_min_frequency > 0:
            policy = self._get_overload_policy(rest_settings)

        allowed_max = rest_max_limit
        if rest_max_limit > 0 and policy:
            allowed_max += policy.extra_day_limit

        min_extension = policy.extra_day_limit if policy and rest_min_consecutive <= 1 else 0
        rest_min_limit = rest_min_frequency + min_extension

        rest_flag = 0
        if rest_max_limit > 0 and new_streak > rest_max_limit:
            rest_flag = 2
            if allowed_max > rest_max_limit and new_streak > allowed_max:
                rest_flag = 3
        elif rest_min_frequency > 0 and new_streak >= rest_min_frequency:
            if new_streak <= rest_min_limit:
                rest_flag = 0
            else:
                required_span = max(rest_settings.rest_min_consecutive_days or 1, 1)
                if not self._can_delay_rest_with_manual(
                    state,
                    current_date,
                    rest_settings,
                    new_streak,
                    required_span,
                ):
                    rest_flag = 1

        return (rest_flag, new_streak)

    def _would_trigger_overtime(
        self,
        state: OperatorState,
        position: PositionDefinition,
        current_date: date,
    ) -> bool:
        if not self.options.honor_rest_rules:
            return False

        rest_settings = position.category
        rest_max = rest_settings.rest_max_consecutive_days or 0
        if rest_max <= 0:
            return False
        new_streak = self._calculate_new_streak(state, current_date)
        return new_streak > rest_max

    def _is_operator_available(
        self,
        state: OperatorState,
        capability: OperatorCapability,
        position: PositionDefinition,
        current_date: date,
    ) -> bool:
        if state.employment_end_date and current_date > state.employment_end_date:
            return False
        if state.employment_start_date and current_date < state.employment_start_date:
            return False
        if not state.operator.is_active:
            return False

        if current_date in state.assigned_dates:
            return False

        if state.is_rest_day(current_date):
            return False

        if state.blocked_until and current_date <= state.blocked_until:
            return False

        required_skill = required_skill_for_complexity(position.complexity)
        operator_skill = capability.skill_score

        if operator_skill < required_skill and not (
            self.options.allow_lower_complexity and position.allow_lower_complexity
        ):
            return False

        category = position.category

        if (
            category
            and category.automatic_rest_days
            and self.options.honor_rest_rules
            and not self._can_override_category_rest(state, position, current_date)
        ):
            return False

        if not self.options.honor_rest_rules:
            return True

        rest_settings = category
        new_streak = self._calculate_new_streak(state, current_date)
        allowed_max = rest_settings.rest_max_consecutive_days or 0
        rest_min_frequency = rest_settings.rest_min_frequency or 0
        rest_min_consecutive = rest_settings.rest_min_consecutive_days or 0
        policy = None
        if rest_min_frequency or allowed_max:
            policy = self._get_overload_policy(rest_settings)

        min_extension = policy.extra_day_limit if policy and rest_min_consecutive <= 1 else 0
        rest_min_limit = rest_min_frequency + min_extension
        if rest_min_frequency and new_streak > rest_min_limit:
            required_span = max(rest_settings.rest_min_consecutive_days or 1, 1)
            if not self._can_delay_rest_with_manual(
                state,
                current_date,
                rest_settings,
                new_streak,
                required_span,
            ):
                return False
        if allowed_max and new_streak > allowed_max:
            policy = policy or self._get_overload_policy(position.category)
            extended_limit = allowed_max + policy.extra_day_limit
            if new_streak > extended_limit:
                return False

        return True

    def _is_category_rest_day(self, position: PositionDefinition, current_date: date) -> bool:
        rest_days = self._category_rest_days.get(position.category_id)
        if not rest_days:
            return False
        return current_date.weekday() in rest_days

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
            alert_level = AssignmentAlertLevel.WARN
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
        self._apply_assignment_effects(state, position, current_date, is_overtime)

        category = position.category
        if category and category.automatic_rest_days and current_date.weekday() in category.automatic_rest_days:
            self._consume_manual_rest_credit(state, category, current_date)

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
            self.sync_rest_periods()

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
    def _is_weekend(target_date: date) -> bool:
        return target_date.weekday() >= 5

    @staticmethod
    def _daterange(start: date, end: date) -> Iterator[date]:
        current = start
        while current <= end:
            yield current
            current += timedelta(days=1)

    # ------------------------------------------------------------------
    # Rest context helpers
    # ------------------------------------------------------------------
    def _apply_rest_context(self) -> None:
        if not self._operator_states:
            return

        operator_ids = list(self._operator_states.keys())

        rest_periods_query = OperatorRestPeriod.objects.filter(operator_id__in=operator_ids).exclude(
            status=RestPeriodStatus.CANCELLED
        )
        if self.options.replace_existing:
            rest_periods_query = rest_periods_query.exclude(
                Q(source=RestPeriodSource.CALENDAR) & Q(calendar_id=self.calendar.id)
            )

        rest_periods = rest_periods_query.order_by("start_date", "end_date")

        for period in rest_periods:
            self._rest_periods[period.operator_id].append(period)

        for operator_id, state in self._operator_states.items():
            periods = self._rest_periods.get(operator_id, [])
            self._initialize_rest_state(state, periods)

    def _load_recent_history(self) -> None:
        if not self._operator_states or not self._positions:
            return

        history_window = self._history_window_days()
        if history_window <= 0:
            return

        history_start = self.calendar.start_date - timedelta(days=history_window)
        operator_ids = list(self._operator_states.keys())

        assignments = (
            ShiftAssignment.objects.select_related("position", "position__category")
            .filter(operator_id__in=operator_ids)
            .exclude(calendar_id=self.calendar.id)
            .filter(date__lt=self.calendar.start_date, date__gte=history_start)
            .order_by("date")
        )

        continuity_cache: Dict[int, PositionContinuity] = {}
        for assignment in assignments:
            state = self._operator_states.get(assignment.operator_id)
            position = assignment.position
            if not state or not position:
                continue
            if assignment.date in state.assigned_dates:
                continue

            self._apply_assignment_effects(state, position, assignment.date, assignment.is_overtime)

            previous = continuity_cache.get(position.id)
            if previous and previous.operator_id == assignment.operator_id and previous.last_date == assignment.date - timedelta(days=1):
                continuity_cache[position.id] = PositionContinuity(
                    operator_id=assignment.operator_id,
                    last_date=assignment.date,
                    streak=previous.streak + 1,
                )
            else:
                continuity_cache[position.id] = PositionContinuity(
                    operator_id=assignment.operator_id,
                    last_date=assignment.date,
                    streak=1,
                )

        self._position_continuity.update(continuity_cache)

    def _history_window_days(self) -> int:
        window = 0
        for position in self._positions:
            category = position.category
            if not category:
                continue
            rest_max = category.rest_max_consecutive_days or 0
            rest_min_freq = category.rest_min_frequency or 0
            rest_post = category.rest_post_shift_days or 0
            rest_min_consecutive = category.rest_min_consecutive_days or 0
            policy = self._get_overload_policy(category) if rest_max else None
            overtime_extension = policy.extra_day_limit if policy else 0
            window = max(
                window,
                rest_max + overtime_extension,
                rest_min_freq + rest_min_consecutive,
                rest_post + rest_min_consecutive,
            )
        return max(window, 0)

    def _next_manual_rest_window(self, state: OperatorState, start_date: date) -> Optional[Tuple[date, date]]:
        if not state.manual_rest_windows:
            return None

        for window_start, window_end in state.manual_rest_windows:
            if window_end < start_date:
                continue
            effective_start = max(window_start, start_date)
            effective_end = window_end
            employment_limit = state.employment_end_date
            if employment_limit and effective_start > employment_limit:
                continue
            if employment_limit and effective_end > employment_limit:
                effective_end = employment_limit
            if effective_start > effective_end:
                continue
            return (effective_start, effective_end)

        return None

    def _can_delay_rest_with_manual(
        self,
        state: OperatorState,
        current_date: date,
        rest_settings: PositionCategory,
        projected_streak: int,
        required_span: int,
    ) -> bool:
        if required_span <= 0:
            required_span = 1

        next_day = current_date + timedelta(days=1)
        manual_window = self._next_manual_rest_window(state, next_day)
        if not manual_window:
            return False

        manual_start, manual_end = manual_window
        manual_length = (manual_end - manual_start).days + 1
        if manual_length < required_span:
            return False

        delay = max(0, (manual_start - next_day).days)

        rest_max = rest_settings.rest_max_consecutive_days or 0
        if rest_max:
            policy = self._get_overload_policy(rest_settings)
            allowed_limit = rest_max + (policy.extra_day_limit if policy else 0)
            if projected_streak + delay > allowed_limit:
                return False
        else:
            max_delay = max(0, (rest_settings.rest_min_consecutive_days or 1) - 1)
            if delay > max_delay:
                return False

        return True

    @staticmethod
    def _manual_week_key(target_date: date) -> Tuple[int, int]:
        iso = target_date.isocalendar()
        return (iso.year, iso.week)

    def _register_manual_rest_credit(self, state: OperatorState, manual_day: date) -> None:
        key = self._manual_week_key(manual_day)
        bucket = state.manual_rest_weekly.setdefault(key, {})
        weekday = manual_day.weekday()
        bucket[weekday] = bucket.get(weekday, 0) + 1

    def _manual_rest_credit_available(
        self,
        state: OperatorState,
        category: PositionCategory,
        current_date: date,
    ) -> int:
        key = self._manual_week_key(current_date)
        counts = state.manual_rest_weekly.get(key)
        if not counts:
            return 0

        automatic_days = set(category.automatic_rest_days or [])
        return sum(count for weekday, count in counts.items() if weekday not in automatic_days)

    def _consume_manual_rest_credit(
        self,
        state: OperatorState,
        category: PositionCategory,
        current_date: date,
    ) -> None:
        key = self._manual_week_key(current_date)
        counts = state.manual_rest_weekly.get(key)
        if not counts:
            return

        automatic_days = set(category.automatic_rest_days or [])
        for weekday in sorted(counts):
            if weekday in automatic_days:
                continue
            remaining = counts[weekday]
            if remaining <= 0:
                continue
            counts[weekday] = remaining - 1
            if counts[weekday] <= 0:
                counts.pop(weekday)
            if not counts:
                state.manual_rest_weekly.pop(key, None)
            return

    def _can_override_category_rest(
        self,
        state: OperatorState,
        position: PositionDefinition,
        current_date: date,
    ) -> bool:
        category = position.category
        if not category or not category.automatic_rest_days:
            return True

        weekday = current_date.weekday()
        if weekday not in category.automatic_rest_days:
            return True

        if state.employment_start_date and current_date < state.employment_start_date:
            return False

        if current_date in state.manual_rest_dates:
            return False

        credit = self._manual_rest_credit_available(state, category, current_date)
        return credit > 0

    def _can_generate_on_category_rest_day(self, position: PositionDefinition, current_date: date) -> bool:
        category = position.category
        if not category or not category.automatic_rest_days:
            return True

        if current_date.weekday() not in category.automatic_rest_days:
            return True

        for capability in self._capabilities_by_category.get(position.category_id, []):
            state = self._operator_states.get(capability.operator_id)
            if not state:
                continue
            if self._can_override_category_rest(state, position, current_date):
                return True

        return False

    def _apply_assignment_effects(
        self,
        state: OperatorState,
        position: PositionDefinition,
        current_date: date,
        is_overtime: bool,
    ) -> None:
        state.register_assignment(current_date, position.shift_type, is_overtime)
        rest_settings = position.category
        if not rest_settings:
            return

        rest_min_frequency = rest_settings.rest_min_frequency or 0
        rest_min_consecutive = rest_settings.rest_min_consecutive_days or 0
        rest_max = rest_settings.rest_max_consecutive_days or 0
        policy = None
        if rest_min_frequency or rest_max:
            policy = self._get_overload_policy(rest_settings)
        min_extension = policy.extra_day_limit if policy and rest_min_consecutive <= 1 else 0
        rest_min_limit = rest_min_frequency + min_extension

        if rest_min_frequency and state.consecutive_streak >= rest_min_limit:
            required_span = max(rest_min_consecutive, 1)
            if not self._can_delay_rest_with_manual(
                state,
                current_date,
                rest_settings,
                state.consecutive_streak,
                required_span,
            ):
                self._extend_rest_block(
                    state,
                    current_date,
                    required_span,
                )

        if rest_max and state.consecutive_streak >= rest_max:
            block_span = rest_settings.rest_post_shift_days + rest_min_consecutive
            if block_span <= 0:
                block_span = max(rest_min_consecutive, 1)
            self._extend_rest_block(state, current_date, block_span)

    def _extend_rest_block(
        self,
        state: OperatorState,
        reference_date: date,
        days: int,
    ) -> None:
        if days <= 0:
            return

        block_until = reference_date + timedelta(days=days)
        if state.employment_end_date and block_until > state.employment_end_date:
            block_until = state.employment_end_date
        if not state.blocked_until or block_until > state.blocked_until:
            state.blocked_until = block_until

        max_offset = days
        if state.employment_end_date:
            remaining = (state.employment_end_date - reference_date).days
            max_offset = min(days, remaining)
        for offset in range(1, max_offset + 1):
            rest_day = reference_date + timedelta(days=offset)
            if state.employment_end_date and rest_day > state.employment_end_date:
                break
            state.rest_dates.add(rest_day)

    def _initialize_rest_state(self, state: OperatorState, periods: List[OperatorRestPeriod]) -> None:
        last_completed_rest: Optional[date] = None
        for period in periods:
            if period.end_date < self.calendar.start_date and period.status in {
                RestPeriodStatus.CONFIRMED,
                RestPeriodStatus.APPROVED,
                RestPeriodStatus.PLANNED,
                RestPeriodStatus.EXPIRED,
            }:
                if not last_completed_rest or period.end_date > last_completed_rest:
                    last_completed_rest = period.end_date

            if period.start_date > self.calendar.end_date:
                continue

            is_manual_period = period.source != RestPeriodSource.CALENDAR
            employment_limit = state.employment_end_date
            credit_start = period.start_date
            credit_end = period.end_date
            if employment_limit and credit_end > employment_limit:
                credit_end = employment_limit

            if is_manual_period and credit_end >= credit_start:
                employment_start = state.employment_start_date
                for manual_day in self._daterange(credit_start, credit_end):
                    if employment_start and manual_day < employment_start:
                        continue
                    self._register_manual_rest_credit(state, manual_day)

            overlap_start = max(period.start_date, self.calendar.start_date)
            overlap_end = min(period.end_date, self.calendar.end_date)
            if overlap_start > overlap_end:
                continue

            if period.status == RestPeriodStatus.CANCELLED:
                continue

            if employment_limit and overlap_start > employment_limit:
                continue

            effective_end = min(overlap_end, employment_limit) if employment_limit else overlap_end
            manual_start: Optional[date] = None
            manual_end: Optional[date] = None
            for day in self._daterange(overlap_start, effective_end):
                state.rest_dates.add(day)
                if is_manual_period:
                    state.manual_rest_dates.add(day)
                    if manual_start is None:
                        manual_start = day
                    manual_end = day

            if is_manual_period and manual_start and manual_end:
                state.manual_rest_windows.append((manual_start, manual_end))

        if last_completed_rest:
            state.last_rest_end = last_completed_rest
            if state.last_rest_end < self.calendar.start_date:
                days_since_rest = (self.calendar.start_date - state.last_rest_end).days - 1
                if days_since_rest > 0:
                    state.consecutive_streak = days_since_rest
                    state.consecutive_day_streak = days_since_rest
                    state.last_assignment_date = self.calendar.start_date - timedelta(days=1)
        elif state.employment_start_date:
            state.last_rest_end = state.employment_start_date - timedelta(days=1)

        if state.manual_rest_windows:
            state.manual_rest_windows.sort(key=lambda window: window[0])

    def sync_rest_periods(self) -> None:
        sync_calendar_rest_periods(
            self.calendar,
            operator_ids=self._operator_states.keys(),
            calendar_dates=self._calendar_dates,
        )


def sync_calendar_rest_periods(
    calendar: ShiftCalendar,
    *,
    operator_ids: Optional[Iterable[int]] = None,
    calendar_dates: Optional[Iterable[date]] = None,
) -> None:
    dates = list(calendar_dates or CalendarScheduler._daterange(calendar.start_date, calendar.end_date))
    if not dates:
        calendar.rest_periods.filter(source=RestPeriodSource.CALENDAR).delete()
        return

    operator_id_set = set(operator_ids or [])

    assignments_by_day: Dict[date, set[int]] = defaultdict(set)
    assignments = calendar.assignments.select_related("operator").values_list("operator_id", "date")
    for operator_id, assignment_date in assignments:
        if operator_id is None:
            continue
        assignments_by_day[assignment_date].add(operator_id)
        operator_id_set.add(operator_id)

    manual_operator_ids = (
        calendar.rest_periods.exclude(source=RestPeriodSource.CALENDAR).values_list("operator_id", flat=True)
    )
    operator_id_set.update(manual_operator_ids)

    employment_limits: Dict[int, Optional[date]] = {}
    if operator_id_set:
        employment_limits = {
            profile.id: profile.employment_end_date
            for profile in UserProfile.objects.filter(id__in=operator_id_set).only("id", "employment_end_date")
        }

    existing_manual_periods = OperatorRestPeriod.objects.filter(operator_id__in=operator_id_set).exclude(
        status=RestPeriodStatus.CANCELLED
    )
    manual_periods_map: Dict[int, List[OperatorRestPeriod]] = defaultdict(list)
    for period in existing_manual_periods:
        manual_periods_map[period.operator_id].append(period)

    calendar.rest_periods.filter(source=RestPeriodSource.CALENDAR).delete()

    new_periods: List[OperatorRestPeriod] = []

    for operator_id in operator_id_set:
        manual_periods = manual_periods_map.get(operator_id, [])
        manual_periods.sort(key=lambda p: (p.start_date, p.end_date))

        employment_limit = employment_limits.get(operator_id)
        relevant_dates = [day for day in dates if employment_limit is None or day <= employment_limit]
        if not relevant_dates:
            continue

        relevant_set = set(relevant_dates)
        manual_day_map: Dict[date, OperatorRestPeriod] = {}
        for period in manual_periods:
            if period.status == RestPeriodStatus.CANCELLED:
                continue

            manual_end = period.end_date
            if employment_limit and manual_end > employment_limit:
                manual_end = employment_limit

            manual_start = period.start_date
            if manual_end < manual_start:
                continue

            for manual_day in CalendarScheduler._daterange(manual_start, manual_end):
                if manual_day not in relevant_set:
                    continue
                manual_day_map[manual_day] = period

        periods_confirmed: Set[int] = set()
        current_start: Optional[date] = None

        for day in relevant_dates:
            working = operator_id in assignments_by_day.get(day, set())
            manual_period = manual_day_map.get(day)

            if working:
                if current_start:
                    new_periods.append(
                        OperatorRestPeriod(
                            operator_id=operator_id,
                            start_date=current_start,
                            end_date=day - timedelta(days=1),
                            status=RestPeriodStatus.CONFIRMED,
                            source=RestPeriodSource.CALENDAR,
                            calendar=calendar,
                        )
                    )
                    current_start = None
                continue

            if manual_period:
                if current_start:
                    new_periods.append(
                        OperatorRestPeriod(
                            operator_id=operator_id,
                            start_date=current_start,
                            end_date=day - timedelta(days=1),
                            status=RestPeriodStatus.CONFIRMED,
                            source=RestPeriodSource.CALENDAR,
                            calendar=calendar,
                        )
                    )
                    current_start = None

                period_id = manual_period.id
                if period_id is not None and period_id not in periods_confirmed:
                    update_fields: List[str] = []
                    if manual_period.status in {RestPeriodStatus.PLANNED, RestPeriodStatus.APPROVED}:
                        manual_period.status = RestPeriodStatus.CONFIRMED
                        update_fields.append("status")
                    if manual_period.calendar_id != calendar.id:
                        manual_period.calendar = calendar
                        update_fields.append("calendar")
                    if update_fields:
                        update_fields.append("updated_at")
                        manual_period.save(update_fields=update_fields)
                    periods_confirmed.add(period_id)
                continue

            if current_start is None:
                current_start = day

        if current_start is not None:
            new_periods.append(
                OperatorRestPeriod(
                    operator_id=operator_id,
                    start_date=current_start,
                    end_date=relevant_dates[-1],
                    status=RestPeriodStatus.CONFIRMED,
                    source=RestPeriodSource.CALENDAR,
                    calendar=calendar,
                )
            )

    if new_periods:
        OperatorRestPeriod.objects.bulk_create(new_periods, batch_size=100)

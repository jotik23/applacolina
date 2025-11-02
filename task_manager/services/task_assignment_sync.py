from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, timedelta
import threading
from typing import DefaultDict, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Set, Tuple

from django.db import transaction
from django.db.models import Case, IntegerField, Value, When

from personal.models import CalendarStatus, ShiftAssignment, UserProfile

from task_manager.models import TaskAssignment, TaskDefinition


_SUPPRESSION_STATE = threading.local()


@contextmanager
def suppress_task_assignment_sync() -> Iterator[None]:
    """Temporarily disable automatic synchronization triggers."""

    current_level = getattr(_SUPPRESSION_STATE, "level", 0)
    _SUPPRESSION_STATE.level = current_level + 1
    try:
        yield
    finally:
        if current_level <= 0:
            if hasattr(_SUPPRESSION_STATE, "level"):
                delattr(_SUPPRESSION_STATE, "level")
        else:
            _SUPPRESSION_STATE.level = current_level


def is_task_assignment_sync_suppressed() -> bool:
    """Return True when synchronization should be skipped."""

    return getattr(_SUPPRESSION_STATE, "level", 0) > 0


@dataclass(frozen=True)
class AssignmentSnapshot:
    date: date
    operator_id: Optional[int]
    position_id: Optional[int]
    farm_id: Optional[int]
    chicken_house_id: Optional[int]
    room_ids: frozenset[int]


@dataclass(frozen=True)
class AssignmentTarget:
    task_definition_id: int
    due_date: date
    collaborator_id: Optional[int]


class TaskRule:
    """In-memory representation of a task definition with cached scope metadata."""

    def __init__(self, task: TaskDefinition):
        self.task = task
        self._farm_ids: frozenset[int] = frozenset(task.farms.values_list("id", flat=True))
        self._house_ids: frozenset[int] = frozenset(task.chicken_houses.values_list("id", flat=True))
        self._room_ids: frozenset[int] = frozenset(task.rooms.values_list("id", flat=True))
        self._weekly_days: frozenset[int] = frozenset(task.weekly_days or [])
        self._month_days: frozenset[int] = frozenset(task.month_days or [])
        self._fortnight_days: frozenset[int] = frozenset(task.fortnight_days or [])
        self._monthly_week_days: frozenset[int] = frozenset(task.monthly_week_days or [])

    @property
    def collaborator(self) -> Optional[UserProfile]:
        return self.task.collaborator

    @property
    def collaborator_id(self) -> Optional[int]:
        return self.task.collaborator_id

    @property
    def position_id(self) -> Optional[int]:
        return self.task.position_id

    def iter_due_dates(self, start_date: date, end_date: date) -> Iterator[date]:
        """Yield every date where the task should be evaluated."""

        if start_date > end_date:
            return

        if not self.task.task_type:
            return

        if self.task.task_type == TaskDefinition.TaskType.ONE_TIME:
            scheduled_for = self.task.scheduled_for
            if scheduled_for and start_date <= scheduled_for <= end_date:
                yield scheduled_for
            return

        if not any(
            (self._weekly_days, self._month_days, self._fortnight_days, self._monthly_week_days)
        ):
            return

        current = start_date
        while current <= end_date:
            if self._matches_recurring_date(current):
                yield current
            current += timedelta(days=1)

    def requires_orphan_on_empty(self) -> bool:
        """Return True when we should persist an orphan assignment if no collaborator matches."""

        return bool(self.task.task_type)

    def fallback_collaborator_id(self, due_date: date) -> Optional[int]:
        """Resolve a collaborator when no shift assignment matched the scope."""

        collaborator = self.task.collaborator
        if not collaborator:
            return None

        if any((self.position_id, self._farm_ids, self._house_ids, self._room_ids)):
            # When there is contextual scope we only assign if a shift matches it.
            return None

        if collaborator.is_active_on(due_date):
            return collaborator.pk
        return None

    def matches_snapshot(self, snapshot: AssignmentSnapshot) -> bool:
        """Return True when the snapshot belongs to the task scope."""

        if self.position_id and snapshot.position_id != self.position_id:
            return False

        if self.collaborator_id and snapshot.operator_id != self.collaborator_id:
            return False

        if self._farm_ids and snapshot.farm_id not in self._farm_ids:
            return False

        if self._house_ids and snapshot.chicken_house_id not in self._house_ids:
            return False

        if self._room_ids and snapshot.room_ids.isdisjoint(self._room_ids):
            return False

        return True

    def _matches_recurring_date(self, current: date) -> bool:
        matches = False

        if self._weekly_days and current.weekday() in self._weekly_days:
            matches = True

        if self._month_days and current.day in self._month_days:
            matches = True

        if self._fortnight_days and self._fortnight_day(current.day) in self._fortnight_days:
            matches = True

        if self._monthly_week_days and self._week_number_in_month(current.day) in self._monthly_week_days:
            matches = True

        return matches

    @staticmethod
    def _fortnight_day(day_of_month: int) -> int:
        # Maps calendar days to a 1-15 cycle (1st fortnight) and 16-30/31 (2nd fortnight).
        return ((day_of_month - 1) % 15) + 1

    @staticmethod
    def _week_number_in_month(day_of_month: int) -> int:
        return ((day_of_month - 1) // 7) + 1


class TaskAssignmentSynchronizer:
    """Synchronize TaskAssignment rows with TaskDefinition and ShiftAssignment information."""

    ACTIVE_CALENDAR_STATUSES: Sequence[str] = (
        CalendarStatus.MODIFIED,
        CalendarStatus.APPROVED,
        CalendarStatus.DRAFT,
    )

    _STATUS_PRIORITY = Case(
        When(calendar__status=CalendarStatus.MODIFIED, then=Value(0)),
        When(calendar__status=CalendarStatus.APPROVED, then=Value(1)),
        When(calendar__status=CalendarStatus.DRAFT, then=Value(2)),
        default=Value(3),
        output_field=IntegerField(),
    )

    def __init__(self, *, start_date: date, end_date: date):
        if start_date > end_date:
            raise ValueError("El rango de fechas es inválido.")
        self.start_date = start_date
        self.end_date = end_date

    def sync(self) -> None:
        """Public entrypoint to synchronize assignments."""

        task_rules = list(self._load_task_rules())
        if not task_rules:
            return

        assignments_by_date = self._load_shift_assignments()
        targets = self._build_targets(task_rules, assignments_by_date)
        if not targets:
            # Even if there were no concrete targets we still orphan existing rows in range.
            self._orphan_existing_without_targets(task_rules)
            return

        self._reconcile_targets(targets, task_rules)

    def _load_task_rules(self) -> Iterator[TaskRule]:
        queryset = (
            TaskDefinition.objects.select_related("position", "collaborator", "status")
            .prefetch_related("farms", "chicken_houses", "rooms")
            .filter(status__is_active=True)
        )
        for task in queryset:
            yield TaskRule(task)

    def _load_shift_assignments(self) -> DefaultDict[date, List[AssignmentSnapshot]]:
        queryset = (
            ShiftAssignment.objects.select_related(
                "calendar",
                "position",
                "position__farm",
                "position__chicken_house",
                "operator",
            )
            .prefetch_related("position__rooms")
            .filter(
                date__range=(self.start_date, self.end_date),
                calendar__status__in=self.ACTIVE_CALENDAR_STATUSES,
            )
            .annotate(_status_priority=self._STATUS_PRIORITY)
            .order_by("date", "_status_priority", "-calendar__updated_at", "-calendar__created_at", "calendar_id")
        )

        assignments_by_date: DefaultDict[date, List[AssignmentSnapshot]] = defaultdict(list)
        seen_position_date: Set[Tuple[int, date]] = set()
        seen_operator_date: Set[Tuple[int, date]] = set()

        for assignment in queryset:
            if not assignment.position_id:
                continue

            position_key = (assignment.position_id, assignment.date)
            operator_key = (assignment.operator_id, assignment.date)

            if position_key in seen_position_date:
                continue

            if assignment.operator_id and operator_key in seen_operator_date:
                continue

            seen_position_date.add(position_key)
            if assignment.operator_id:
                seen_operator_date.add(operator_key)

            room_ids = frozenset(room.pk for room in assignment.position.rooms.all())
            snapshot = AssignmentSnapshot(
                date=assignment.date,
                operator_id=assignment.operator_id,
                position_id=assignment.position_id,
                farm_id=getattr(assignment.position.farm, "pk", None),
                chicken_house_id=getattr(assignment.position.chicken_house, "pk", None),
                room_ids=room_ids,
            )
            assignments_by_date[assignment.date].append(snapshot)

        return assignments_by_date

    def _build_targets(
        self,
        task_rules: Sequence[TaskRule],
        assignments_by_date: Mapping[date, Sequence[AssignmentSnapshot]],
    ) -> Dict[Tuple[int, date, Optional[int]], AssignmentTarget]:
        targets: Dict[Tuple[int, date, Optional[int]], AssignmentTarget] = {}

        for rule in task_rules:
            for due_date in rule.iter_due_dates(self.start_date, self.end_date):
                snapshots = assignments_by_date.get(due_date, [])
                matched_snapshots = [snapshot for snapshot in snapshots if rule.matches_snapshot(snapshot)]

                if matched_snapshots:
                    for snapshot in matched_snapshots:
                        key = (rule.task.pk, due_date, snapshot.operator_id)
                        targets[key] = AssignmentTarget(
                            task_definition_id=rule.task.pk,
                            due_date=due_date,
                            collaborator_id=snapshot.operator_id,
                        )
                    continue

                fallback_collaborator_id = rule.fallback_collaborator_id(due_date)
                key = (rule.task.pk, due_date, fallback_collaborator_id)

                if fallback_collaborator_id is not None or rule.requires_orphan_on_empty():
                    targets[key] = AssignmentTarget(
                        task_definition_id=rule.task.pk,
                        due_date=due_date,
                        collaborator_id=fallback_collaborator_id,
                    )

        return targets

    def _reconcile_targets(
        self,
        targets: Dict[Tuple[int, date, Optional[int]], AssignmentTarget],
        task_rules: Sequence[TaskRule],
    ) -> None:
        task_ids = {rule.task.pk for rule in task_rules}
        existing_qs = (
            TaskAssignment.objects.select_related("collaborator")
            .filter(
                task_definition_id__in=task_ids,
                due_date__range=(self.start_date, self.end_date),
            )
            .order_by("due_date", "task_definition_id")
        )
        existing = list(existing_qs)

        existing_by_key: Dict[Tuple[int, date, Optional[int]], TaskAssignment] = {}
        existing_by_task_date: DefaultDict[Tuple[int, date], List[TaskAssignment]] = defaultdict(list)
        matched_ids: Set[int] = set()

        for assignment in existing:
            key = (assignment.task_definition_id, assignment.due_date, assignment.collaborator_id)
            existing_by_key[key] = assignment
            existing_by_task_date[(assignment.task_definition_id, assignment.due_date)].append(assignment)

        with transaction.atomic():
            for key, target in sorted(targets.items(), key=lambda item: (item[1].due_date, item[1].task_definition_id)):
                assignment = existing_by_key.get(key)
                if assignment:
                    matched_ids.add(assignment.pk)
                    continue

                task_date_key = (target.task_definition_id, target.due_date)
                pool = existing_by_task_date.get(task_date_key, [])

                if target.collaborator_id is not None:
                    reusable = next(
                        (candidate for candidate in pool if candidate.pk not in matched_ids and candidate.collaborator_id is None),
                        None,
                    )
                    if reusable:
                        previous_key = (reusable.task_definition_id, reusable.due_date, reusable.collaborator_id)
                        reusable.collaborator_id = target.collaborator_id
                        reusable.save(update_fields=["collaborator", "updated_at"])
                        matched_ids.add(reusable.pk)
                        existing_by_key.pop(previous_key, None)
                        existing_by_key[(reusable.task_definition_id, reusable.due_date, reusable.collaborator_id)] = reusable
                        continue

                if target.collaborator_id is None:
                    reusable = next(
                        (candidate for candidate in pool if candidate.pk not in matched_ids),
                        None,
                    )
                    if reusable and reusable.collaborator_id is not None:
                        previous_key = (reusable.task_definition_id, reusable.due_date, reusable.collaborator_id)
                        reusable.collaborator = None
                        reusable.save(update_fields=["collaborator", "updated_at"])
                        matched_ids.add(reusable.pk)
                        existing_by_key.pop(previous_key, None)
                        existing_by_key[(reusable.task_definition_id, reusable.due_date, None)] = reusable
                        continue

                created = TaskAssignment.objects.create(
                    task_definition_id=target.task_definition_id,
                    due_date=target.due_date,
                    collaborator_id=target.collaborator_id,
                )
                matched_ids.add(created.pk)
                existing_by_key[(created.task_definition_id, created.due_date, created.collaborator_id)] = created
                existing_by_task_date[task_date_key].append(created)

            self._mark_orphans(existing, matched_ids)

    def _mark_orphans(self, existing_assignments: Sequence[TaskAssignment], matched_ids: Set[int]) -> None:
        for assignment in existing_assignments:
            if assignment.pk in matched_ids:
                continue
            if assignment.collaborator_id is None:
                continue
            assignment.collaborator = None
            assignment.save(update_fields=["collaborator", "updated_at"])

    def _orphan_existing_without_targets(self, task_rules: Sequence[TaskRule]) -> None:
        task_ids = {rule.task.pk for rule in task_rules}
        if not task_ids:
            return
        queryset = TaskAssignment.objects.filter(
            task_definition_id__in=task_ids,
            due_date__range=(self.start_date, self.end_date),
            collaborator__isnull=False,
        )
        with transaction.atomic():
            for assignment in queryset:
                assignment.collaborator = None
                assignment.save(update_fields=["collaborator", "updated_at"])


def sync_task_assignments(*, start_date: date, end_date: date) -> None:
    """Convenience wrapper to execute the synchronizer."""

    synchronizer = TaskAssignmentSynchronizer(start_date=start_date, end_date=end_date)
    synchronizer.sync()

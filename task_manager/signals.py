from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Iterable, Set

from django.conf import settings
from django.db import transaction
from django.db.models.signals import m2m_changed, post_delete, post_save
from django.dispatch import receiver
from django.utils import timezone

from personal.models import ShiftAssignment
from task_manager.models import TaskDefinition
from task_manager.services import is_task_assignment_sync_suppressed, sync_task_assignments

logger = logging.getLogger(__name__)

SYNC_PAST_DAYS = getattr(settings, "TASK_ASSIGNMENT_SYNC_PAST_DAYS", 7)
SYNC_FUTURE_DAYS = getattr(settings, "TASK_ASSIGNMENT_SYNC_FUTURE_DAYS", 30)
SYNC_MAX_FUTURE_DAYS = getattr(settings, "TASK_ASSIGNMENT_SYNC_MAX_FUTURE_DAYS", 120)

def _schedule_range_sync(start_date: date, end_date: date) -> None:
    if is_task_assignment_sync_suppressed():
        return

    if start_date > end_date:
        start_date, end_date = end_date, start_date

    def _run_sync() -> None:
        try:
            sync_task_assignments(start_date=start_date, end_date=end_date)
        except Exception:  # pragma: no cover - defensive logging
            logger.exception(
                "Falló la sincronización de asignaciones de tareas para el rango %s - %s",
                start_date,
                end_date,
            )

    transaction.on_commit(_run_sync)


def _resolve_task_sync_range(task: TaskDefinition) -> tuple[date, date]:
    today = timezone.localdate()

    if task.task_type == TaskDefinition.TaskType.ONE_TIME and task.scheduled_for:
        scheduled = task.scheduled_for
        return scheduled, scheduled

    start_date = today - timedelta(days=max(SYNC_PAST_DAYS, 0))
    end_date = today + timedelta(days=max(SYNC_FUTURE_DAYS, 0))

    if task.scheduled_for:
        if task.scheduled_for < start_date:
            start_date = task.scheduled_for
        if task.scheduled_for > end_date:
            end_date = task.scheduled_for

    max_window = max(SYNC_MAX_FUTURE_DAYS, SYNC_FUTURE_DAYS)
    if max_window > 0:
        max_end = start_date + timedelta(days=max_window)
        if end_date > max_end:
            end_date = max_end

    return start_date, end_date


def _sync_task_definition(task: TaskDefinition) -> None:
    start_date, end_date = _resolve_task_sync_range(task)
    _schedule_range_sync(start_date, end_date)


@receiver(post_save, sender=TaskDefinition)
def handle_task_definition_change(sender, instance: TaskDefinition, raw: bool = False, **kwargs) -> None:
    if raw:
        return
    _sync_task_definition(instance)


@receiver(m2m_changed, sender=TaskDefinition.farms.through)
@receiver(m2m_changed, sender=TaskDefinition.chicken_houses.through)
@receiver(m2m_changed, sender=TaskDefinition.rooms.through)
def handle_task_definition_scope_change(sender, instance: TaskDefinition, action: str, **kwargs) -> None:
    if not action or not action.startswith("post_"):
        return
    _sync_task_definition(instance)


def _extract_dates_for_assignment(instance: ShiftAssignment) -> Set[date]:
    dates: Set[date] = set()
    if instance.date:
        dates.add(instance.date)

    previous = getattr(instance, "_previous_assignment", None)
    if previous and previous.date:
        dates.add(previous.date)

    return dates


def _sync_for_dates(dates: Iterable[date]) -> None:
    unique_dates = sorted({value for value in dates if value})
    for due_date in unique_dates:
        _schedule_range_sync(due_date, due_date)


@receiver(post_save, sender=ShiftAssignment)
def handle_shift_assignment_change(sender, instance: ShiftAssignment, raw: bool = False, **kwargs) -> None:
    if raw or is_task_assignment_sync_suppressed():
        return
    _sync_for_dates(_extract_dates_for_assignment(instance))


@receiver(post_delete, sender=ShiftAssignment)
def handle_shift_assignment_deletion(sender, instance: ShiftAssignment, **kwargs) -> None:
    if is_task_assignment_sync_suppressed():
        return
    if instance.date:
        _schedule_range_sync(instance.date, instance.date)

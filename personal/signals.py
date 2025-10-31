from __future__ import annotations

from typing import Any, Optional

from django.db.models.signals import post_delete, post_save, pre_delete, pre_save
from django.dispatch import receiver

from .models import (
    AssignmentChangeLog,
    OperatorRestPeriod,
    RestPeriodSource,
    RestPeriodStatus,
    ShiftAssignment,
    ShiftCalendar,
)
from .services import sync_calendar_rest_periods


@receiver(pre_save, sender=ShiftAssignment)
def cache_previous_assignment_state(sender: type[ShiftAssignment], instance: ShiftAssignment, **kwargs: Any) -> None:
    if not instance.pk:
        return
    try:
        instance._previous_assignment = sender.objects.get(pk=instance.pk)  # type: ignore[attr-defined]
    except sender.DoesNotExist:
        instance._previous_assignment = None  # type: ignore[attr-defined]


@receiver(post_save, sender=ShiftAssignment)
def log_assignment_update(sender: type[ShiftAssignment], instance: ShiftAssignment, created: bool, **kwargs: Any) -> None:
    previous: Optional[ShiftAssignment] = getattr(instance, "_previous_assignment", None)

    if created:
        AssignmentChangeLog.objects.create(
            assignment=instance,
            changed_by=None,
            change_type=AssignmentChangeLog.ChangeType.CREATED,
            previous_operator=None,
            new_operator=instance.operator,
            details={
                "auto": instance.is_auto_assigned,
                "alert": instance.alert_level,
                "calendar_id": instance.calendar_id,
            },
        )
        sync_calendar_rest_periods(instance.calendar)
        return

    # Updated assignment
    if previous and previous.operator_id != instance.operator_id:
        AssignmentChangeLog.objects.create(
            assignment=instance,
            changed_by=None,
            change_type=AssignmentChangeLog.ChangeType.UPDATED,
            previous_operator=previous.operator,
            new_operator=instance.operator,
            details={
                "auto": instance.is_auto_assigned,
                "alert": instance.alert_level,
                "calendar_id": instance.calendar_id,
            },
        )
    sync_calendar_rest_periods(instance.calendar)


@receiver(post_delete, sender=ShiftAssignment)
def log_assignment_deletion(sender: type[ShiftAssignment], instance: ShiftAssignment, **kwargs: Any) -> None:
    AssignmentChangeLog.objects.create(
        assignment=None,
        changed_by=None,
        change_type=AssignmentChangeLog.ChangeType.DELETED,
        previous_operator=instance.operator,
        new_operator=None,
        details={
            "auto": instance.is_auto_assigned,
            "alert": instance.alert_level,
            "assignment_id": instance.pk,
            "calendar_id": instance.calendar_id,
        },
    )
    sync_calendar_rest_periods(instance.calendar)


@receiver(pre_delete, sender=ShiftCalendar)
def cleanup_rest_periods(sender: type[ShiftCalendar], instance: ShiftCalendar, **kwargs: Any) -> None:
    OperatorRestPeriod.objects.filter(
        calendar=instance,
        source=RestPeriodSource.CALENDAR,
    ).delete()

    OperatorRestPeriod.objects.filter(calendar=instance).exclude(
        source=RestPeriodSource.CALENDAR
    ).update(status=RestPeriodStatus.APPROVED, calendar=None)

from __future__ import annotations

from typing import Any, Iterable

from .models import ShiftCalendar


def get_recent_calendars_payload(
    *,
    limit: int = 3,
    exclude_ids: Iterable[int] | None = None,
) -> list[dict[str, Any]]:
    """
    Build a lightweight payload with the most recent calendars by start date.
    """

    queryset = ShiftCalendar.objects.order_by("-start_date", "-created_at")
    if exclude_ids:
        queryset = queryset.exclude(pk__in=list(exclude_ids))

    calendars = list(queryset[:limit])
    return [
        {
            "id": calendar.id,
            "display_name": calendar.name or f"Calendario {calendar.start_date:%d/%m/%Y}",
            "start_date": calendar.start_date,
            "end_date": calendar.end_date,
            "status": calendar.status,
            "status_label": calendar.get_status_display(),
        }
        for calendar in calendars
    ]

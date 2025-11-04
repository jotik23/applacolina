"""Template context helpers for exposing global application settings."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.utils import timezone


def timezone_settings(request):
    """Expose timezone metadata for templates and frontend scripts."""

    tz_name = getattr(settings, "TIME_ZONE", "UTC")
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        tz_name = "UTC"
        tz = ZoneInfo(tz_name)

    now = timezone.now()
    if timezone.is_naive(now):
        aware_now = datetime.now(tz)
    else:
        aware_now = now.astimezone(tz)

    offset = aware_now.utcoffset()
    offset_minutes = int(offset.total_seconds() // 60) if offset else 0

    return {
        "APP_TIME_ZONE": tz_name,
        "APP_TIME_ZONE_OFFSET_MINUTES": offset_minutes,
    }


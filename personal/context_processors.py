from __future__ import annotations

from .forms import CalendarGenerationForm


def quick_create(request):
    """
    Provide default context required by the global quick-create actions.

    Individual views can override these values by passing their own context
    when rendering templates.
    """

    return {
        "calendar_generation_form": CalendarGenerationForm(),
        "calendar_generation_recent_calendars": [],
    }


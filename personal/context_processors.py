from __future__ import annotations

from django.urls import reverse

from task_manager.forms import TaskDefinitionQuickCreateForm

from .forms import CalendarGenerationForm
from .selectors import get_recent_calendars_payload


def quick_create(request):
    """
    Provide default context required by the global quick-create actions.

    Individual views can override these values by passing their own context
    when rendering templates.
    """

    return {
        "calendar_generation_form": CalendarGenerationForm(),
        "calendar_generation_recent_calendars": get_recent_calendars_payload(),
        "task_definition_form": TaskDefinitionQuickCreateForm(),
        "task_definition_create_url": reverse("task_manager:definition-create"),
        "task_definition_detail_url_template": reverse(
            "task_manager:definition-detail",
            kwargs={"pk": 0},
        ),
        "task_definition_update_url_template": reverse(
            "task_manager:definition-update",
            kwargs={"pk": 0},
        ),
    }

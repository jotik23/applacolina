from __future__ import annotations

from django.http import HttpResponse
from django.shortcuts import redirect

from personal.views import CalendarConfiguratorView
from task_manager.views import TaskManagerHomeView


class BaseConfigurationConfiguratorView(CalendarConfiguratorView):
    default_step_slug: str | None = None

    def get(self, request, *args, **kwargs) -> HttpResponse:
        if self.default_step_slug and not request.GET.get("step"):
            params = request.GET.copy()
            params["step"] = self.default_step_slug
            query = params.urlencode()
            target = f"{request.path}"
            if query:
                target = f"{target}?{query}"
            return redirect(target)
        return super().get(request, *args, **kwargs)


class ConfigurationCollaboratorsView(BaseConfigurationConfiguratorView):
    configuration_active_submenu = "collaborators"
    default_step_slug = "collaborators"


class ConfigurationPositionsView(BaseConfigurationConfiguratorView):
    configuration_active_submenu = "positions"
    default_step_slug = "positions"


class ConfigurationTaskManagerView(TaskManagerHomeView):
    configuration_active_submenu = "tasks"

from django.views.generic import TemplateView


class TaskManagerHomeView(TemplateView):
    """Render a placeholder landing page for the task manager module."""

    template_name = "task_manager/index.html"


task_manager_home_view = TaskManagerHomeView.as_view()


from django.views import generic

from applacolina.mixins import StaffRequiredMixin


class AdministrationHomeView(StaffRequiredMixin, generic.TemplateView):
    """Render the Administration landing page."""

    template_name = 'administration/index.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault('administration_active_submenu', 'purchases')
        return context


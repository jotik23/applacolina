from django.views import generic

from applacolina.mixins import StaffRequiredMixin

from .services.purchases import get_dashboard_state


class AdministrationHomeView(StaffRequiredMixin, generic.TemplateView):
    """Render the Administration landing page."""

    template_name = 'administration/index.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault('administration_active_submenu', 'purchases')
        state = get_dashboard_state(
            scope_code=self.request.GET.get('scope'),
            panel_code=self.request.GET.get('panel'),
            purchase_pk=_parse_int(self.request.GET.get('purchase')),
        )
        context.update(
            purchases_scope=state.scope,
            purchases_scopes=state.scopes,
            purchases_list=state.purchases,
            purchases_panel=state.panel,
            purchases_recent_activity=state.recent_activity,
        )
        return context


def _parse_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

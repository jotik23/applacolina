from __future__ import annotations

from decimal import Decimal

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect
from django.views.generic import TemplateView
from django.db.models import Sum, Case, When, DecimalField, F

from applacolina.mixins import StaffRequiredMixin
from administration.services.purchase_payment_resets import (
    get_purchases_missing_payment_amount_queryset,
    reset_missing_payment_amounts,
)
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


class ConfigurationCommandsView(StaffRequiredMixin, TemplateView):
    template_name = "configuration/commands.html"
    configuration_active_submenu = "commands"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault("configuration_active_submenu", self.configuration_active_submenu)
        pending_qs = get_purchases_missing_payment_amount_queryset().annotate(
            approved_amount=Case(
                When(invoice_total__gt=0, then=F('invoice_total')),
                default=F('estimated_total'),
                output_field=DecimalField(max_digits=14, decimal_places=2),
            )
        )
        pending_total = pending_qs.aggregate(total=Sum('approved_amount'))['total'] or Decimal('0')
        pending_entries = list(pending_qs.order_by('timeline_code'))
        context.update(
            pending_payment_resets_count=len(pending_entries),
            pending_payment_resets_total=pending_total,
            pending_payment_resets=pending_entries,
        )
        return context

    def post(self, request, *args, **kwargs):
        action = (request.POST.get("command_action") or "").strip()
        if action == "reset_purchase_payments":
            updated = reset_missing_payment_amounts()
            if updated:
                messages.success(request, f"Se actualizaron {updated} solicitudes con su valor pagado.")
            else:
                messages.info(request, "No se encontraron solicitudes pendientes por actualizar.")
            return redirect("configuration:commands")
        messages.error(request, "Comando no soportado.")
        return redirect("configuration:commands")

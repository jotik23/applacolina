from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.db.models import Sum
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views import generic

from applacolina.mixins import StaffRequiredMixin

from .forms import (
    InventoryFilterForm,
    InventoryResetForm,
    ManualConsumptionForm,
    ProductConsumptionConfigForm,
)
from .models import (
    InventoryScope,
    ProductConsumptionConfig,
    ProductInventoryBalance,
    ProductInventoryEntry,
)


class InventoryDashboardView(StaffRequiredMixin, generic.TemplateView):
    template_name = "inventory/dashboard.html"

    def get(self, request, *args, **kwargs):
        return self.render_to_response(self.get_context_data(**kwargs))

    def post(self, request, *args, **kwargs):
        action = request.POST.get("form_action")
        if action == "manual_consumption":
            return self._handle_manual_consumption()
        if action == "inventory_reset":
            return self._handle_inventory_reset()
        if action == "consumption_config":
            return self._handle_consumption_config()
        messages.error(request, "Acción no soportada.")
        return redirect(self._default_url())

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        filter_form = kwargs.get("filter_form") or self._build_filter_form()
        manual_form = kwargs.get("manual_form") or ManualConsumptionForm(
            initial={"product": self._selected_product_id(filter_form)}
        )
        reset_form = kwargs.get("reset_form") or InventoryResetForm(
            initial={
                "product": self._selected_product_id(filter_form),
                "scope": InventoryScope.CHICKEN_HOUSE,
            }
        )
        config_form = kwargs.get("config_form") or ProductConsumptionConfigForm(
            initial={"product": self._selected_product_id(filter_form)}
        )
        entries = []
        totals = {"incoming": Decimal("0.00"), "outgoing": Decimal("0.00")}
        selected_product = None
        configs = []
        scope_label = "Selecciona un producto"
        if filter_form.is_bound and filter_form.is_valid():
            selected_product = filter_form.cleaned_data["product"]
            entries = self._fetch_entries(filter_form)
            scope_label = self._current_scope_label(filter_form.cleaned_data)
            self._annotate_running_balances(
                entries,
                self._current_balance(filter_form.cleaned_data),
            )
            totals = self._build_totals(entries)
            configs = self._load_consumption_configs(selected_product)
        context.update(
            filter_form=filter_form,
            manual_form=manual_form,
            reset_form=reset_form,
            config_form=config_form,
            inventory_entries=entries,
            inventory_totals=totals,
            inventory_selected_product=selected_product,
            inventory_consumption_configs=configs,
            inventory_scope_label=scope_label,
        )
        context.setdefault("active_submenu", "product_inventory")
        resolver = getattr(self.request, "resolver_match", None)
        if resolver and resolver.namespace == "inventory":
            context["home_active_tab"] = "inventory"
        return context

    def _handle_manual_consumption(self):
        form = ManualConsumptionForm(self.request.POST)
        filter_form = self._build_filter_form()
        if form.is_valid():
            form.save(self.request.user)
            messages.success(self.request, "Consumo registrado correctamente.")
            return redirect(self._default_url(product_id=form.cleaned_data["product"].pk))
        messages.error(self.request, "Corrige los errores del consumo manual.")
        return self.render_to_response(
            self.get_context_data(manual_form=form, filter_form=filter_form)
        )

    def _handle_inventory_reset(self):
        form = InventoryResetForm(self.request.POST)
        filter_form = self._build_filter_form()
        if form.is_valid():
            form.save(self.request.user)
            messages.success(self.request, "Inventario actualizado con el conteo físico.")
            return redirect(self._default_url(product_id=form.cleaned_data["product"].pk))
        messages.error(self.request, "Confirma y corrige los datos del reseteo.")
        return self.render_to_response(
            self.get_context_data(reset_form=form, filter_form=filter_form)
        )

    def _handle_consumption_config(self):
        form = ProductConsumptionConfigForm(self.request.POST)
        filter_form = self._build_filter_form()
        if form.is_valid():
            instance = form.save(commit=False)
            if getattr(self.request.user, "is_authenticated", False):
                instance.created_by = self.request.user
            instance.save()
            messages.success(self.request, "Configuración de consumo guardada.")
            return redirect(self._default_url(product_id=instance.product_id))
        messages.error(self.request, "Revisa la configuración antes de guardar.")
        return self.render_to_response(
            self.get_context_data(config_form=form, filter_form=filter_form)
        )

    def _build_filter_form(self) -> InventoryFilterForm:
        default_start = timezone.localdate() - timedelta(days=30)
        if self.request.GET:
            data = self.request.GET.copy()
            if not data.get("start_date"):
                data["start_date"] = default_start.isoformat()
            return InventoryFilterForm(data)
        return InventoryFilterForm(initial={"start_date": default_start})

    def _fetch_entries(self, form: InventoryFilterForm):
        cleaned = form.cleaned_data
        qs = ProductInventoryEntry.objects.select_related(
            "product",
            "farm",
            "chicken_house",
            "recorded_by",
            "executed_by",
        ).filter(product=cleaned["product"])
        chicken_house = cleaned.get("chicken_house")
        farm = cleaned.get("farm")
        if chicken_house:
            qs = qs.filter(chicken_house=chicken_house)
        elif farm:
            qs = qs.filter(farm=farm, scope__in=[InventoryScope.FARM, InventoryScope.CHICKEN_HOUSE])
        start_date = cleaned.get("start_date")
        if start_date:
            qs = qs.filter(effective_date__gte=start_date)
        return list(qs.order_by("-effective_date", "-id")[:400])

    def _build_totals(self, entries: list[ProductInventoryEntry]) -> dict[str, Decimal]:
        incoming = sum((entry.quantity_in for entry in entries), Decimal("0.00"))
        outgoing = sum((entry.quantity_out for entry in entries), Decimal("0.00"))
        return {"incoming": incoming, "outgoing": outgoing, "net": incoming - outgoing}

    def _current_balance(self, cleaned_data) -> Decimal:
        product = cleaned_data.get("product")
        if not product:
            return Decimal("0.00")
        balances = ProductInventoryBalance.objects.filter(product=product)
        chicken_house = cleaned_data.get("chicken_house")
        farm = cleaned_data.get("farm")
        if chicken_house:
            balances = balances.filter(chicken_house=chicken_house)
        elif farm:
            balances = balances.filter(farm=farm)
        total = balances.aggregate(total=Sum("quantity"))["total"]
        return total or Decimal("0.00")

    def _annotate_running_balances(
        self,
        entries: list[ProductInventoryEntry],
        current_total: Decimal,
    ) -> None:
        running = current_total
        for entry in entries:
            entry.cardex_balance = running
            delta = entry.quantity_in - entry.quantity_out
            running -= delta

    def _current_scope_label(self, cleaned_data) -> str:
        chicken_house = cleaned_data.get("chicken_house")
        if chicken_house:
            return f"Galpón · {chicken_house.name}"
        farm = cleaned_data.get("farm")
        if farm:
            return f"Granja · {farm.name}"
        return "Empresa completa"

    def _load_consumption_configs(self, product):
        qs = (
            ProductConsumptionConfig.objects.select_related("farm", "chicken_house", "product")
            .order_by("-start_date", "-id")
        )
        if product:
            qs = qs.filter(product=product)
        return list(qs[:200])

    def _default_url(self, *, product_id: int | None = None) -> str:
        base = reverse("inventory:dashboard")
        if not product_id:
            return base
        return f"{base}?product={product_id}"

    def _selected_product_id(self, form: InventoryFilterForm) -> int | None:
        if form.is_bound and form.is_valid():
            return form.cleaned_data["product"].pk
        product_value = self.request.GET.get("product")
        return int(product_value) if product_value and product_value.isdigit() else None


class HomeInventoryDashboardView(InventoryDashboardView):
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["home_active_tab"] = "inventory"
        return context

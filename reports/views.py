from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from django.contrib.auth import get_user_model
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views import generic

from applacolina.mixins import StaffRequiredMixin
from production.models import EggDispatch, EggDispatchDestination

from .services.inventory_comparison import build_inventory_comparison
from .services.key_metrics import DEFAULT_RANGE_DAYS, build_key_metrics

UserProfile = get_user_model()


class KeyMetricsDashboardView(StaffRequiredMixin, generic.TemplateView):
    template_name = "reports/dashboard.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        start_date, end_date = self._resolve_range()
        metrics_result = build_key_metrics(start_date, end_date)
        range_days = (end_date - start_date).days + 1
        context.update(
            {
                "reports_active_submenu": "key_metrics",
                "selected_start_date": start_date,
                "selected_end_date": end_date,
                "range_days": range_days,
                "metrics": metrics_result.metrics,
                "charts_dataset": metrics_result.charts,
                "quick_ranges": self._build_quick_ranges(end_date),
                "default_range_days": DEFAULT_RANGE_DAYS,
            }
        )
        return context

    def _resolve_range(self) -> tuple[date, date]:
        params = self.request.GET
        today = timezone.localdate()
        default_end = today
        default_start = today - timedelta(days=DEFAULT_RANGE_DAYS - 1)
        start_raw = (params.get("start_date") or "").strip()
        end_raw = (params.get("end_date") or "").strip()
        start_date = parse_date(start_raw) if start_raw else default_start
        end_date = parse_date(end_raw) if end_raw else default_end
        if not start_date:
            start_date = default_start
        if not end_date:
            end_date = default_end
        if start_date > end_date:
            start_date, end_date = end_date, start_date
        return start_date, end_date

    def _build_quick_ranges(self, end_date: date) -> list[dict[str, Any]]:
        presets = [7, 30, 60, 90]
        ranges: list[dict[str, Any]] = []
        for days in presets:
            start = end_date - timedelta(days=days - 1)
            ranges.append(
                {
                    "label": f"Últimos {days} días" if days != 7 else "Últimos 7 días",
                    "start": start,
                    "end": end_date,
                    "days": days,
                }
            )
        return ranges


class InventoryComparisonView(StaffRequiredMixin, generic.TemplateView):
    template_name = "reports/inventory_comparison.html"
    DEFAULT_PRODUCTION_RANGE = 30
    DEFAULT_DISPATCH_RANGE = 30
    DEFAULT_SALES_RANGE = 30

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        production_start, production_end = self._resolve_range(
            prefix="production",
            default_days=self.DEFAULT_PRODUCTION_RANGE,
        )
        dispatch_start, dispatch_end = self._resolve_range(
            prefix="dispatch",
            default_days=self.DEFAULT_DISPATCH_RANGE,
            fallback=(production_start, production_end),
        )
        sales_start, sales_end = self._resolve_range(
            prefix="sales",
            default_days=self.DEFAULT_SALES_RANGE,
            fallback=(production_start, production_end),
        )
        dispatch_filters = self._resolve_dispatch_filters()
        seller_options = self._get_dispatch_seller_options()
        destination_choices = EggDispatchDestination.choices
        seller_label = next(
            (option["label"] for option in seller_options if option["id"] == dispatch_filters["seller_id"]),
            "",
        )
        destination_label = dict(destination_choices).get(dispatch_filters["destination"], "")
        comparison = build_inventory_comparison(
            production_start=production_start,
            production_end=production_end,
            dispatch_start=dispatch_start,
            dispatch_end=dispatch_end,
            dispatch_seller_id=dispatch_filters["seller_id"],
            dispatch_destination=dispatch_filters["destination"],
            sales_start=sales_start,
            sales_end=sales_end,
        )
        context.update(
            {
                "reports_active_submenu": "inventory_comparison",
                "filters": {
                    "production_start": production_start,
                    "production_end": production_end,
                    "dispatch_start": dispatch_start,
                    "dispatch_end": dispatch_end,
                    "sales_start": sales_start,
                    "sales_end": sales_end,
                    "dispatch_seller": dispatch_filters["seller_id"],
                    "dispatch_destination": dispatch_filters["destination"],
                    "dispatch_seller_label": seller_label,
                    "dispatch_destination_label": destination_label,
                },
                "production_quick_ranges": self._build_quick_ranges(
                    end_date=production_end,
                    prefix="production",
                ),
                "sales_quick_ranges": self._build_quick_ranges(
                    end_date=sales_end,
                    prefix="sales",
                ),
                "dispatch_filter_options": {
                    "sellers": seller_options,
                    "destinations": destination_choices,
                },
                "comparison": comparison,
            }
        )
        return context

    def _resolve_range(
        self,
        *,
        prefix: str,
        default_days: int,
        fallback: tuple[date, date] | None = None,
    ) -> tuple[date, date]:
        params = self.request.GET
        today = timezone.localdate()
        fallback_start, fallback_end = fallback if fallback else (None, None)
        default_end = fallback_end or today
        default_start = fallback_start or (default_end - timedelta(days=default_days - 1))
        start_raw = (params.get(f"{prefix}_start") or "").strip()
        end_raw = (params.get(f"{prefix}_end") or "").strip()
        start_date = parse_date(start_raw) if start_raw else default_start
        end_date = parse_date(end_raw) if end_raw else default_end
        if not start_date:
            start_date = default_start
        if not end_date:
            end_date = default_end
        if start_date > end_date:
            start_date, end_date = end_date, start_date
        return start_date, end_date

    def _build_quick_ranges(self, *, end_date: date, prefix: str) -> list[dict[str, Any]]:
        presets = [7, 15, 30, 60]
        ranges: list[dict[str, Any]] = []
        for days in presets:
            start = end_date - timedelta(days=days - 1)
            ranges.append(
                {
                    "label": f"Últimos {days} días" if days != 7 else "Últimos 7 días",
                    "start": start,
                    "end": end_date,
                    "days": days,
                    "prefix": prefix,
                }
            )
        return ranges

    def _resolve_dispatch_filters(self) -> dict[str, Any]:
        seller_value = self.request.GET.get("dispatch_seller")
        destination_value = (self.request.GET.get("dispatch_destination") or "").strip()
        seller_id = self._parse_int(seller_value)
        valid_destinations = {choice[0] for choice in EggDispatchDestination.choices}
        destination = destination_value if destination_value in valid_destinations else ""
        return {
            "seller_id": seller_id,
            "destination": destination,
        }

    def _get_dispatch_seller_options(self) -> list[dict[str, Any]]:
        seller_ids = list(
            EggDispatch.objects.exclude(seller__isnull=True)
            .values_list("seller_id", flat=True)
            .distinct()
        )
        if not seller_ids:
            return []
        sellers = (
            UserProfile.objects.filter(id__in=seller_ids)
            .order_by("apellidos", "nombres", "id")
        )
        return [
            {
                "id": seller.id,
                "label": seller.get_full_name() or str(seller),
            }
            for seller in sellers
        ]

    @staticmethod
    def _parse_int(value: str | None) -> int | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

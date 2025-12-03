from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Sequence

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views import generic

from applacolina.mixins import StaffRequiredMixin
from administration.models import PurchaseRequest, PurchasingExpenseType, Sale, Supplier
from production.models import EggDispatch, EggDispatchDestination

from .services.inventory_comparison import build_inventory_comparison
from .services.key_metrics import DEFAULT_RANGE_DAYS, build_key_metrics
from .services.purchase_insights import PurchaseInsightsFilters, build_purchase_insights

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
            sales_seller_id=dispatch_filters["seller_id"],
            sales_destination=dispatch_filters["destination"],
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
        dispatch_ids = set(
            EggDispatch.objects.exclude(seller__isnull=True)
            .values_list("seller_id", flat=True)
            .distinct()
        )
        sale_ids = set(
            Sale.objects.exclude(seller__isnull=True)
            .values_list("seller_id", flat=True)
            .distinct()
        )
        seller_ids = sorted(dispatch_ids.union(sale_ids))
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


class PurchaseSpendingReportView(StaffRequiredMixin, generic.TemplateView):
    template_name = "reports/purchases_insights.html"
    DEFAULT_RANGE_DAYS = 90
    TABLE_LIMIT = 150
    BADGE_CLASSES = {
        "slate": "bg-slate-100 text-slate-700 ring-slate-200",
        "amber": "bg-amber-100 text-amber-900 ring-amber-200",
        "indigo": "bg-indigo-100 text-indigo-900 ring-indigo-200",
        "orange": "bg-orange-100 text-orange-900 ring-orange-200",
        "emerald": "bg-emerald-100 text-emerald-900 ring-emerald-200",
        "cyan": "bg-cyan-100 text-cyan-900 ring-cyan-200",
        "blue": "bg-blue-100 text-blue-900 ring-blue-200",
    }
    SORT_OPTIONS = (
        ("-date", "Más recientes"),
        ("date", "Más antiguos"),
        ("-amount", "Mayor monto"),
        ("amount", "Menor monto"),
        ("-variance", "Mayor desviación"),
        ("variance", "Menor desviación"),
        ("supplier", "Proveedor (A→Z)"),
        ("-supplier", "Proveedor (Z→A)"),
    )

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        filter_payload = self._resolve_filters()
        filters = PurchaseInsightsFilters(
            start_date=filter_payload["start_date"],
            end_date=filter_payload["end_date"],
            statuses=tuple(filter_payload["statuses"]),
            requester_ids=tuple(filter_payload["requesters"]),
            supplier_ids=tuple(filter_payload["suppliers"]),
            category_ids=tuple(filter_payload["categories"]),
            min_amount=filter_payload["amount_min"],
            max_amount=filter_payload["amount_max"],
            search=filter_payload["search"],
        )
        insights = build_purchase_insights(filters)
        ordering = self._resolve_ordering()
        sorted_rows = self._sort_rows(insights.rows, ordering)
        table_rows = self._serialize_rows(sorted_rows)
        table_grouped_rows = self._group_rows_by_category(table_rows)
        range_days = (filter_payload["end_date"] - filter_payload["start_date"]).days + 1
        context.update(
            {
                "reports_active_submenu": "purchases",
                "filters": filter_payload,
                "applied_filters_count": self._count_active_filters(filter_payload),
                "filter_options": self._build_filter_options(),
                "sort_options": self.SORT_OPTIONS,
                "current_sort": ordering,
                "summary": insights.summary,
                "category_breakdown": insights.category_breakdown,
                "area_breakdown": insights.area_breakdown,
                "payment_method_breakdown": insights.payment_method_breakdown,
                "supplier_breakdown": insights.supplier_breakdown,
                "requester_breakdown": insights.requester_breakdown,
                "support_breakdown": insights.support_breakdown,
                "status_breakdown": insights.status_breakdown,
                "timeline": insights.timeline,
                "alerts": insights.optimization_alerts,
                "table_rows": table_rows,
                "table_grouped_rows": table_grouped_rows,
                "table_total_rows": len(table_rows),
                "charts_dataset": insights.chart_payload,
                "quick_ranges": self._build_quick_ranges(filter_payload["end_date"]),
                "range_days": range_days,
            }
        )
        return context

    def _resolve_filters(self) -> dict[str, Any]:
        params = self.request.GET
        today = timezone.localdate()
        default_end = today
        default_start = today - timedelta(days=self.DEFAULT_RANGE_DAYS - 1)
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
        statuses = self._filter_statuses(params.getlist("status"))
        requester_ids = self._parse_int_list(params.getlist("requester"))
        supplier_ids = self._parse_int_list(params.getlist("supplier"))
        category_ids = self._parse_int_list(params.getlist("category"))
        amount_min = self._parse_decimal(params.get("amount_min"))
        amount_max = self._parse_decimal(params.get("amount_max"))
        search = (params.get("search") or "").strip()
        return {
            "start_date": start_date,
            "end_date": end_date,
            "statuses": statuses,
            "requesters": requester_ids,
            "suppliers": supplier_ids,
            "categories": category_ids,
            "amount_min": amount_min,
            "amount_max": amount_max,
            "search": search,
        }

    def _build_filter_options(self) -> dict[str, Any]:
        requester_ids = (
            PurchaseRequest.objects.exclude(requester__isnull=True)
            .values_list("requester_id", flat=True)
            .distinct()
        )
        requester_options = (
            UserProfile.objects.filter(id__in=requester_ids)
            .order_by("apellidos", "nombres", "id")
        )
        suppliers_ids = (
            PurchaseRequest.objects.exclude(supplier__isnull=True)
            .values_list("supplier_id", flat=True)
            .distinct()
        )
        suppliers = Supplier.objects.filter(id__in=suppliers_ids).order_by("name")
        categories = PurchasingExpenseType.objects.select_related("parent_category").order_by("name")
        grouped_categories: dict[int, dict[str, Any]] = {}
        for category in categories:
            if category.parent_category_id:
                parent_id = category.parent_category_id
                parent_name = category.parent_category.name if category.parent_category else "Generales"
            else:
                parent_id = category.id
                parent_name = category.name
            bucket = grouped_categories.setdefault(
                parent_id,
                {"label": parent_name, "options": []},
            )
            if parent_id == category.id:
                bucket["options"].insert(
                    0,
                    {
                        "value": category.id,
                        "label": category.name,
                        "is_parent": True,
                    },
                )
            else:
                bucket["options"].append(
                    {
                        "value": category.id,
                        "label": category.name,
                        "is_parent": False,
                    },
                )
        category_options = [
            {
                "label": bucket["label"],
                "options": sorted(bucket["options"], key=lambda option: option["label"].lower()),
            }
            for bucket in sorted(grouped_categories.values(), key=lambda item: item["label"].lower())
        ]
        return {
            "statuses": [
                {"value": code, "label": label}
                for code, label in PurchaseRequest.Status.choices
            ],
            "requesters": [
                {"value": profile.pk, "label": profile.get_full_name() or profile.email or str(profile)}
                for profile in requester_options
            ],
            "suppliers": [{"value": supplier.pk, "label": supplier.name} for supplier in suppliers],
            "categories": category_options,
        }

    def _resolve_ordering(self) -> str:
        value = (self.request.GET.get("sort") or "").strip()
        allowed_values = {option[0] for option in self.SORT_OPTIONS}
        if value not in allowed_values:
            value = "-date"
        return value

    def _sort_rows(self, rows: Any, ordering: str):
        reverse = ordering.startswith("-")
        key = ordering.lstrip("-")
        status_priority = {code: index for index, (code, _) in enumerate(PurchaseRequest.Status.choices)}
        if key == "amount":
            key_func = lambda row: row.executed_total  # noqa: E731
        elif key == "variance":
            key_func = lambda row: row.variance  # noqa: E731
        elif key == "supplier":
            key_func = lambda row: row.supplier_name.lower()  # noqa: E731
        elif key == "status":
            key_func = lambda row: status_priority.get(row.status, 0)  # noqa: E731
        else:
            key_func = lambda row: row.analysis_date  # noqa: E731
        return sorted(rows, key=key_func, reverse=reverse)

    def _serialize_rows(self, rows: Any) -> list[dict[str, Any]]:
        base_url = reverse("administration:purchases")
        serialized: list[dict[str, Any]] = []
        for row in rows:
            badge_class = self.BADGE_CLASSES.get(row.status_palette, self.BADGE_CLASSES["slate"])
            detail_url = self._build_detail_url(base_url, row.pk, row.action_panel)
            serialized.append(
                {
                    "pk": row.pk,
                    "code": row.timeline_code,
                    "name": row.name,
                    "supplier": row.supplier_name,
                    "requester": row.requester_name,
                    "category": row.category_name,
                    "parent_category": row.parent_category_name,
                    "status_label": row.status_label,
                    "status_badge": badge_class,
                    "currency": row.currency,
                    "estimated_total": row.estimated_total,
                    "executed_total": row.executed_total,
                    "variance": row.variance,
                    "analysis_date": row.analysis_date,
                    "area_label": row.area_label,
                    "scope_label": row.scope_label,
                    "detail_url": detail_url,
                    "variance_positive": row.variance > Decimal("0"),
                }
            )
        return serialized

    def _group_rows_by_category(self, rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            category = row.get("parent_category") or "Sin categoría"
            entry = grouped.setdefault(
                category,
                {"category": category, "total": Decimal("0"), "rows": []},
            )
            entry["rows"].append(row)
            entry["total"] += row.get("executed_total") or Decimal("0")
        return list(grouped.values())

    def _build_detail_url(self, base_url: str, purchase_id: int, panel: str | None) -> str:
        if panel:
            return f"{base_url}?purchase={purchase_id}&panel={panel}"
        return f"{base_url}?purchase={purchase_id}"

    @staticmethod
    def _parse_int_list(raw_values: Sequence[str]) -> list[int]:
        parsed: list[int] = []
        for value in raw_values:
            try:
                parsed.append(int(value))
            except (TypeError, ValueError):
                continue
        return parsed

    @staticmethod
    def _parse_decimal(value: str | None) -> Decimal | None:
        if value is None:
            return None
        value = value.strip().replace(",", ".")
        if not value:
            return None
        try:
            return Decimal(value)
        except (ArithmeticError, ValueError):
            return None

    @staticmethod
    def _count_active_filters(filters: dict[str, Any]) -> int:
        count = 0
        if filters["statuses"]:
            count += 1
        if filters["requesters"]:
            count += 1
        if filters["suppliers"]:
            count += 1
        if filters["categories"]:
            count += 1
        if filters["amount_min"] is not None or filters["amount_max"] is not None:
            count += 1
        if filters["search"]:
            count += 1
        return count

    @staticmethod
    def _filter_statuses(values: Sequence[str]) -> list[str]:
        valid = {code for code, _ in PurchaseRequest.Status.choices}
        return [value for value in values if value in valid]

    @staticmethod
    def _build_quick_ranges(end_date: date) -> list[dict[str, Any]]:
        presets = [30, 60, 90, 180]
        ranges: list[dict[str, Any]] = []
        for days in presets:
            start = end_date - timedelta(days=days - 1)
            ranges.append(
                {
                    "label": f"Últimos {days} días",
                    "start": start,
                    "end": end_date,
                }
            )
        return ranges

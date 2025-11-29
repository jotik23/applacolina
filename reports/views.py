from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views import generic

from applacolina.mixins import StaffRequiredMixin

from .services.key_metrics import DEFAULT_RANGE_DAYS, build_key_metrics


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

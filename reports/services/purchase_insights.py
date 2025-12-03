from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Iterable, Sequence

from django.db import models
from django.db.models import DecimalField, Q
from django.db.models.functions import Coalesce, Cast
from administration.models import PurchaseRequest
from administration.services.purchases import ACTION_BY_STATUS, STATUS_BADGES

DECIMAL_FIELD = DecimalField(max_digits=14, decimal_places=2)
DATE_FIELD = models.DateField()
ZERO = Decimal("0.00")


@dataclass(frozen=True)
class PurchaseInsightsFilters:
    start_date: date | None
    end_date: date | None
    statuses: Sequence[str]
    requester_ids: Sequence[int]
    supplier_ids: Sequence[int]
    category_ids: Sequence[int]
    min_amount: Decimal | None
    max_amount: Decimal | None
    search: str | None


@dataclass(frozen=True)
class PurchaseInsightsRow:
    pk: int
    timeline_code: str
    name: str
    supplier_name: str
    supplier_id: int | None
    requester_name: str
    requester_id: int | None
    category_id: int | None
    category_name: str
    parent_category_id: int | None
    parent_category_name: str
    status: str
    status_label: str
    status_palette: str
    currency: str
    estimated_total: Decimal
    executed_total: Decimal
    variance: Decimal
    analysis_date: date
    area_label: str
    scope_label: str
    action_panel: str | None


@dataclass(frozen=True)
class PurchaseInsightsResult:
    rows: Sequence[PurchaseInsightsRow]
    summary: dict[str, Any]
    category_breakdown: list[dict[str, Any]]
    status_breakdown: list[dict[str, Any]]
    supplier_breakdown: list[dict[str, Any]]
    requester_breakdown: list[dict[str, Any]]
    timeline: list[dict[str, Any]]
    optimization_alerts: list[dict[str, str]]
    chart_payload: dict[str, Any]


def build_purchase_insights(filters: PurchaseInsightsFilters) -> PurchaseInsightsResult:
    queryset = _base_queryset()
    queryset = _apply_filters(queryset, filters)
    purchases = list(queryset)
    rows = tuple(_build_rows(purchases))
    summary = _build_summary(rows)
    category_breakdown = _build_category_breakdown(rows, summary["executed_amount"])
    status_breakdown = _build_status_breakdown(rows, summary["executed_amount"])
    supplier_breakdown = _build_dimension_breakdown(
        rows,
        summary["executed_amount"],
        key_selector=lambda row: row.supplier_id or 0,
        label_selector=lambda row: row.supplier_name,
    )
    requester_breakdown = _build_dimension_breakdown(
        rows,
        summary["executed_amount"],
        key_selector=lambda row: row.requester_id or 0,
        label_selector=lambda row: row.requester_name,
    )
    timeline = _build_timeline(rows)
    optimization_alerts = _build_alerts(
        summary=summary,
        status_breakdown=status_breakdown,
        supplier_breakdown=supplier_breakdown,
        category_breakdown=category_breakdown,
    )
    chart_payload = _build_chart_payload(
        category_breakdown=category_breakdown,
        status_breakdown=status_breakdown,
        timeline=timeline,
    )
    return PurchaseInsightsResult(
        rows=rows,
        summary=summary,
        category_breakdown=category_breakdown,
        status_breakdown=status_breakdown,
        supplier_breakdown=supplier_breakdown,
        requester_breakdown=requester_breakdown,
        timeline=timeline,
        optimization_alerts=optimization_alerts,
        chart_payload=chart_payload,
    )


def _base_queryset():
    return (
        PurchaseRequest.objects.select_related(
            "supplier",
            "requester",
            "expense_type",
            "expense_type__parent_category",
        )
        .prefetch_related(
            "items__scope_farm",
            "items__scope_chicken_house__farm",
        )
        .annotate(
            analysis_date=Coalesce(
                "payment_date",
                "invoice_date",
                "purchase_date",
                "order_date",
                Cast("created_at", output_field=DATE_FIELD),
                output_field=DATE_FIELD,
            ),
            executed_total=Coalesce(
                "invoice_total",
                "payment_amount",
                "estimated_total",
                output_field=DECIMAL_FIELD,
            ),
            parent_category_id=Coalesce(
                "expense_type__parent_category_id",
                "expense_type_id",
                output_field=models.IntegerField(),
            ),
            parent_category_name=Coalesce(
                "expense_type__parent_category__name",
                "expense_type__name",
                output_field=models.CharField(max_length=255),
            ),
        )
    )


def _apply_filters(queryset, filters: PurchaseInsightsFilters):
    if filters.start_date:
        queryset = queryset.filter(analysis_date__gte=filters.start_date)
    if filters.end_date:
        queryset = queryset.filter(analysis_date__lte=filters.end_date)
    if filters.statuses:
        queryset = queryset.filter(status__in=filters.statuses)
    if filters.requester_ids:
        queryset = queryset.filter(requester_id__in=filters.requester_ids)
    if filters.supplier_ids:
        queryset = queryset.filter(supplier_id__in=filters.supplier_ids)
    if filters.category_ids:
        queryset = queryset.filter(
            Q(expense_type_id__in=filters.category_ids)
            | Q(expense_type__parent_category_id__in=filters.category_ids)
        )
    if filters.min_amount is not None:
        queryset = queryset.filter(executed_total__gte=filters.min_amount)
    if filters.max_amount is not None:
        queryset = queryset.filter(executed_total__lte=filters.max_amount)
    search = (filters.search or "").strip()
    if search:
        queryset = queryset.filter(
            Q(timeline_code__icontains=search)
            | Q(name__icontains=search)
            | Q(description__icontains=search)
            | Q(supplier__name__icontains=search)
            | Q(expense_type__name__icontains=search)
            | Q(scope_batch_code__icontains=search)
        )
    return queryset.order_by("-analysis_date", "-created_at")


def _build_rows(purchases: Iterable[PurchaseRequest]) -> Iterable[PurchaseInsightsRow]:
    for purchase in purchases:
        supplier = purchase.supplier
        requester = purchase.requester
        supplier_name = supplier.name if supplier else "—"
        requester_name = _display_user(requester)
        estimated_total = purchase.estimated_total or ZERO
        executed_total = getattr(purchase, "executed_total", None) or ZERO
        variance = executed_total - estimated_total
        area_label = purchase.area_label
        scope_label = purchase.scope_label
        status_label, status_palette = STATUS_BADGES.get(
            purchase.status,
            ("", "slate"),
        )
        action = ACTION_BY_STATUS.get(purchase.status)
        analysis_date = getattr(purchase, "analysis_date", None)
        yield PurchaseInsightsRow(
            pk=purchase.pk,
            timeline_code=purchase.timeline_code,
            name=purchase.name,
            supplier_name=supplier_name,
            supplier_id=getattr(supplier, "pk", None),
            requester_name=requester_name,
            requester_id=getattr(requester, "pk", None),
            category_id=purchase.expense_type_id,
            category_name=purchase.expense_type.name if purchase.expense_type else "Sin categoría",
            parent_category_id=getattr(purchase, "parent_category_id", None),
            parent_category_name=getattr(purchase, "parent_category_name", "") or "Sin categoría",
            status=purchase.status,
            status_label=status_label,
            status_palette=status_palette,
            currency=purchase.currency or "COP",
            estimated_total=estimated_total,
            executed_total=executed_total,
            variance=variance,
            analysis_date=analysis_date or purchase.created_at.date(),
            area_label=area_label,
            scope_label=scope_label,
            action_panel=action.panel if action else None,
        )


def _build_summary(rows: Sequence[PurchaseInsightsRow]) -> dict[str, Any]:
    total_requests = len(rows)
    executed_amount = ZERO
    planned_amount = ZERO
    pending_amount = ZERO
    over_budget_amount = ZERO
    over_budget_count = 0
    pending_count = 0
    for row in rows:
        executed_amount += row.executed_total
        planned_amount += row.estimated_total
        if row.status not in PurchaseRequest.POST_PAYMENT_STATUSES:
            pending_amount += row.executed_total
            pending_count += 1
        if row.variance > ZERO:
            over_budget_amount += row.variance
            over_budget_count += 1
    variance_total = executed_amount - planned_amount
    average_ticket = executed_amount / Decimal(total_requests) if total_requests else ZERO
    return {
        "total_requests": total_requests,
        "executed_amount": executed_amount,
        "planned_amount": planned_amount,
        "variance_total": variance_total,
        "pending_amount": pending_amount,
        "pending_count": pending_count,
        "over_budget_amount": over_budget_amount,
        "over_budget_count": over_budget_count,
        "average_ticket": average_ticket,
    }


def _build_category_breakdown(
    rows: Sequence[PurchaseInsightsRow],
    overall_amount: Decimal,
) -> list[dict[str, Any]]:
    totals: dict[int | None, dict[str, Any]] = {}
    for row in rows:
        bucket = totals.setdefault(
            row.parent_category_id,
            {
                "name": row.parent_category_name,
                "amount": ZERO,
                "count": 0,
                "children": {},
            },
        )
        bucket["amount"] += row.executed_total
        bucket["count"] += 1
        child_bucket = bucket["children"].setdefault(
            row.category_id,
            {
                "name": row.category_name,
                "amount": ZERO,
                "count": 0,
            },
        )
        child_bucket["amount"] += row.executed_total
        child_bucket["count"] += 1
    ordered: list[dict[str, Any]] = []
    for category_id, payload in sorted(
        totals.items(),
        key=lambda item: item[1]["amount"],
        reverse=True,
    ):
        amount = payload["amount"]
        children = [
            {
                "name": child_data["name"],
                "amount": child_data["amount"],
                "count": child_data["count"],
                "share_global": _share(child_data["amount"], overall_amount),
                "share_parent": _share(child_data["amount"], amount),
            }
            for child_data in sorted(
                payload["children"].values(),
                key=lambda child: child["amount"],
                reverse=True,
            )
        ]
        ordered.append(
            {
                "id": category_id,
                "name": payload["name"],
                "amount": amount,
                "count": payload["count"],
                "share": _share(amount, overall_amount),
                "children": children,
            }
        )
    return ordered


def _build_status_breakdown(
    rows: Sequence[PurchaseInsightsRow],
    overall_amount: Decimal,
) -> list[dict[str, Any]]:
    ordered_statuses = [status for status, _ in PurchaseRequest.Status.choices]
    totals: dict[str, dict[str, Any]] = {
        status: {"count": 0, "amount": ZERO}
        for status in ordered_statuses
    }
    for row in rows:
        payload = totals.setdefault(row.status, {"count": 0, "amount": ZERO})
        payload["count"] += 1
        payload["amount"] += row.executed_total
    breakdown: list[dict[str, Any]] = []
    for status in ordered_statuses:
        label, palette = STATUS_BADGES.get(status, ("", "slate"))
        payload = totals.get(status, {"count": 0, "amount": ZERO})
        breakdown.append(
            {
                "status": status,
                "label": label,
                "palette": palette,
                "count": payload["count"],
                "amount": payload["amount"],
                "share": _share(payload["amount"], overall_amount),
            }
        )
    return breakdown


def _build_dimension_breakdown(
    rows: Sequence[PurchaseInsightsRow],
    overall_amount: Decimal,
    *,
    key_selector,
    label_selector,
    limit: int = 8,
) -> list[dict[str, Any]]:
    totals: dict[int, dict[str, Any]] = {}
    for row in rows:
        key = key_selector(row)
        if not key:
            continue
        bucket = totals.setdefault(
            key,
            {
                "label": label_selector(row),
                "amount": ZERO,
                "count": 0,
            },
        )
        bucket["amount"] += row.executed_total
        bucket["count"] += 1
    ordered = sorted(
        totals.values(),
        key=lambda item: item["amount"],
        reverse=True,
    )
    payloads: list[dict[str, Any]] = []
    for bucket in ordered[:limit]:
        payloads.append(
            {
                "label": bucket["label"],
                "amount": bucket["amount"],
                "count": bucket["count"],
                "share": _share(bucket["amount"], overall_amount),
            }
        )
    return payloads


def _build_timeline(rows: Sequence[PurchaseInsightsRow]) -> list[dict[str, Any]]:
    buckets: dict[date, dict[str, Decimal]] = {}
    for row in rows:
        month_key = row.analysis_date.replace(day=1)
        bucket = buckets.setdefault(
            month_key,
            {"committed": ZERO, "executed": ZERO},
        )
        bucket["committed"] += row.estimated_total
        bucket["executed"] += row.executed_total
    points: list[dict[str, Any]] = []
    for month, payload in sorted(buckets.items()):
        label = month.strftime("%b %Y").title()
        points.append(
            {
                "label": label,
                "month": month,
                "committed": payload["committed"],
                "executed": payload["executed"],
            }
        )
    return points


def _build_alerts(
    *,
    summary: dict[str, Any],
    status_breakdown: Sequence[dict[str, Any]],
    supplier_breakdown: Sequence[dict[str, Any]],
    category_breakdown: Sequence[dict[str, Any]],
) -> list[dict[str, str]]:
    alerts: list[dict[str, str]] = []
    if summary["pending_amount"] > ZERO and summary["pending_count"]:
        alerts.append(
            {
                "title": "Pagos pendientes",
                "body": (
                    f"{summary['pending_count']} compras concentran "
                    f"{_format_currency(summary['pending_amount'])} en estados previos al pago."
                ),
                "tone": "warning",
            }
        )
    if summary["over_budget_count"]:
        alerts.append(
            {
                "title": "Desviaciones sobre el presupuesto",
                "body": (
                    f"{summary['over_budget_count']} compras superaron su estimado por "
                    f"{_format_currency(summary['over_budget_amount'])}."
                ),
                "tone": "alert",
            }
        )
    if supplier_breakdown:
        top_supplier = supplier_breakdown[0]
        if top_supplier["share"] >= 35:
            alerts.append(
                {
                    "title": "Dependencia de proveedor",
                    "body": (
                        f"{top_supplier['label']} explica {top_supplier['share']:.1f}% del gasto analizado. "
                        "Negocia condiciones o diversifica para reducir riesgos."
                    ),
                    "tone": "info",
                }
            )
    if category_breakdown:
        leader = category_breakdown[0]
        if leader["share"] >= 40:
            alerts.append(
                {
                    "title": "Concentración por categoría",
                    "body": (
                        f"La categoría {leader['name']} agrupa {leader['share']:.1f}% del gasto. "
                        "Valida si existen eficiencias disponibles."
                    ),
                    "tone": "info",
                }
            )
    if not alerts and status_breakdown:
        top_status = max(status_breakdown, key=lambda row: row["share"])
        if top_status["share"] >= 25:
            alerts.append(
                {
                    "title": "Flujo operativo",
                    "body": (
                        f"{top_status['label']} concentra {top_status['share']:.1f}% del monto. "
                        "Asegura capacidad para mover esas compras al siguiente hito."
                    ),
                    "tone": "info",
                }
            )
    return alerts[:3]


def _build_chart_payload(
    *,
    category_breakdown: Sequence[dict[str, Any]],
    status_breakdown: Sequence[dict[str, Any]],
    timeline: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "categoryShare": [
            {"label": row["name"], "value": float(row["amount"])}
            for row in category_breakdown[:8]
        ],
        "statusShare": {
            "labels": [row["label"] for row in status_breakdown],
            "amounts": [float(row["amount"]) for row in status_breakdown],
            "counts": [row["count"] for row in status_breakdown],
        },
        "timeline": {
            "labels": [point["label"] for point in timeline],
            "committed": [float(point["committed"]) for point in timeline],
            "executed": [float(point["executed"]) for point in timeline],
        },
    }


def _share(amount: Decimal, overall: Decimal) -> float:
    if overall <= ZERO:
        return 0.0
    return float((amount / overall) * Decimal("100"))


def _display_user(user) -> str:
    if not user:
        return "—"
    get_full_name = getattr(user, "get_full_name", None)
    if callable(get_full_name):
        value = (get_full_name() or "").strip()
        if value:
            return value
    for attr in ("email", "username"):
        value = getattr(user, attr, "")
        if value:
            return value
    return str(user)


def _format_currency(amount: Decimal) -> str:
    return f"${amount:,.0f}".replace(",", ".")

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from django.db.models import Sum

from administration.models import Sale, SaleItem, SaleProductType
from production.models import EggClassificationBatch, EggClassificationEntry, EggDispatchItem, EggType
from production.services.egg_classification import ORDERED_EGG_TYPES

ZERO = Decimal("0.00")
TWO_PLACES = Decimal("0.01")


@dataclass(frozen=True)
class StageSummary:
    slug: str
    label: str
    total: Decimal
    note: str
    delta: Decimal | None = None
    delta_label: str | None = None
    accent: str = "slate"


@dataclass(frozen=True)
class InventoryTypeRow:
    type_code: str
    label: str
    classified: Decimal
    dispatched: Decimal
    sold: Decimal
    inventory_gap: Decimal
    dispatch_gap: Decimal
    average_price: Decimal
    inventory_cost: Decimal
    dispatch_gap_cost: Decimal


@dataclass(frozen=True)
class InsightCard:
    title: str
    description: str
    delta: Decimal
    cost: Decimal
    tone: str


@dataclass(frozen=True)
class InventoryComparisonResult:
    production_days: int
    sales_days: int
    stages: list[StageSummary]
    type_rows: list[InventoryTypeRow]
    price_rows: list[dict[str, Any]]
    insights: list[InsightCard]
    totals: dict[str, Decimal]
    chart_payload: dict[str, Any]


def build_inventory_comparison(
    *,
    production_start: date,
    production_end: date,
    sales_start: date,
    sales_end: date,
) -> InventoryComparisonResult:
    if production_start > production_end:
        production_start, production_end = production_end, production_start
    if sales_start > sales_end:
        sales_start, sales_end = sales_end, sales_start

    production_summary = _production_summary(production_start, production_end)
    classification_summary = _classification_breakdown(production_start, production_end)
    dispatch_summary = _dispatch_breakdown(production_start, production_end)
    sales_summary = _sales_breakdown(sales_start, sales_end)

    production_total = production_summary["total"]
    classification_total = classification_summary["total"]
    dispatch_total = dispatch_summary["total"]
    sales_total = sales_summary["total"]
    global_avg_price = sales_summary["global_avg_price"]

    production_gap = production_total - classification_total
    inventory_gap_total = classification_total - dispatch_total
    dispatch_gap_total = dispatch_total - sales_total
    production_gap_cost = production_gap * global_avg_price

    type_rows, inventory_cost_total, dispatch_cost_total = _compose_type_rows(
        classification_summary["by_type"],
        dispatch_summary["by_type"],
        sales_summary["by_type"],
        sales_summary["avg_prices"],
        global_avg_price,
    )

    price_rows = _build_price_rows(sales_summary["avg_prices"])

    stages = [
        StageSummary(
            slug="production",
            label="Producido",
            total=production_total,
            note=_pluralize(production_summary["records"], "registro de producción"),
            accent="slate",
        ),
        StageSummary(
            slug="classification",
            label="Clasificado",
            total=classification_total,
            note=_pluralize(classification_summary["batches"], "lote clasificado"),
            delta=classification_total - production_total,
            delta_label="vs. producción",
            accent="amber",
        ),
        StageSummary(
            slug="dispatch",
            label="Despachado",
            total=dispatch_total,
            note=_pluralize(dispatch_summary["dispatches"], "despacho"),
            delta=dispatch_total - classification_total,
            delta_label="vs. clasificado",
            accent="sky",
        ),
        StageSummary(
            slug="sales",
            label="Vendido",
            total=sales_total,
            note=_pluralize(sales_summary["orders"], "venta confirmada"),
            delta=sales_total - dispatch_total,
            delta_label="vs. despachos",
            accent="emerald",
        ),
    ]

    insights = _build_insights(
        production_gap=production_gap,
        production_gap_cost=production_gap_cost,
        inventory_gap=inventory_gap_total,
        inventory_cost=inventory_cost_total,
        dispatch_gap=dispatch_gap_total,
        dispatch_cost=dispatch_cost_total,
    )

    production_days = (production_end - production_start).days + 1
    sales_days = (sales_end - sales_start).days + 1

    totals = {
        "production_total": production_total,
        "classification_total": classification_total,
        "dispatch_total": dispatch_total,
        "sales_total": sales_total,
        "production_gap": production_gap,
        "inventory_gap": inventory_gap_total,
        "dispatch_gap": dispatch_gap_total,
        "production_gap_cost": production_gap_cost,
        "inventory_gap_cost": inventory_cost_total,
        "dispatch_gap_cost": dispatch_cost_total,
        "global_avg_price": global_avg_price,
    }

    chart_payload = {
        "stageTotals": {
            "labels": [stage.label for stage in stages],
            "data": [float(stage.total) for stage in stages],
        },
        "typeComparative": {
            "labels": [row.label for row in type_rows],
            "classified": [float(row.classified) for row in type_rows],
            "dispatched": [float(row.dispatched) for row in type_rows],
            "sold": [float(row.sold) for row in type_rows],
        },
    }

    return InventoryComparisonResult(
        production_days=production_days,
        sales_days=sales_days,
        stages=stages,
        type_rows=type_rows,
        price_rows=price_rows,
        insights=insights,
        totals=totals,
        chart_payload=chart_payload,
    )


def _production_summary(start: date, end: date) -> dict[str, Any]:
    queryset = EggClassificationBatch.objects.filter(production_record__date__range=(start, end))
    aggregates = queryset.aggregate(total=Sum("reported_cartons"))
    total = Decimal(aggregates.get("total") or ZERO)
    return {
        "total": total,
        "records": queryset.count(),
    }


def _classification_breakdown(start: date, end: date) -> dict[str, Any]:
    queryset = EggClassificationEntry.objects.filter(batch__production_record__date__range=(start, end))
    totals: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for row in queryset.values("egg_type").annotate(total=Sum("cartons")):
        egg_type = row["egg_type"]
        totals[egg_type] = Decimal(row["total"] or ZERO)
    total = sum(totals.values(), ZERO)
    batch_count = queryset.values("batch_id").distinct().count()
    return {
        "total": total,
        "by_type": dict(totals),
        "batches": batch_count,
    }


def _dispatch_breakdown(start: date, end: date) -> dict[str, Any]:
    queryset = EggDispatchItem.objects.filter(dispatch__date__range=(start, end))
    totals: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for row in queryset.values("egg_type").annotate(total=Sum("cartons")):
        egg_type = row["egg_type"]
        totals[egg_type] = Decimal(row["total"] or ZERO)
    total = sum(totals.values(), ZERO)
    dispatch_count = queryset.values("dispatch_id").distinct().count()
    return {
        "total": total,
        "by_type": dict(totals),
        "dispatches": dispatch_count,
    }


def _sales_breakdown(start: date, end: date) -> dict[str, Any]:
    queryset = SaleItem.objects.filter(
        sale__date__range=(start, end),
        sale__status__in=(Sale.Status.CONFIRMED, Sale.Status.PAID),
        product_type__in=ORDERED_EGG_TYPES,
    )
    totals: dict[str, Decimal] = defaultdict(lambda: ZERO)
    price_totals: dict[str, Decimal] = defaultdict(lambda: ZERO)
    total_qty = ZERO
    total_value = ZERO
    for row in queryset.values("product_type", "quantity", "unit_price"):
        product_type = row["product_type"]
        quantity = Decimal(row["quantity"] or ZERO)
        unit_price = Decimal(row["unit_price"] or ZERO)
        if quantity <= ZERO:
            continue
        totals[product_type] += quantity
        subtotal = quantity * unit_price
        price_totals[product_type] += subtotal
        total_qty += quantity
        total_value += subtotal

    avg_prices: dict[str, Decimal] = {}
    for code, value in price_totals.items():
        quantity = totals.get(code, ZERO)
        if quantity > ZERO:
            avg_prices[code] = (value / quantity).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

    global_avg_price = (total_value / total_qty).quantize(TWO_PLACES, rounding=ROUND_HALF_UP) if total_qty > ZERO else ZERO
    order_count = queryset.values("sale_id").distinct().count()
    total = sum(totals.values(), ZERO)
    return {
        "total": total,
        "by_type": dict(totals),
        "orders": order_count,
        "avg_prices": avg_prices,
        "global_avg_price": global_avg_price,
    }


def _compose_type_rows(
    classification_totals: dict[str, Decimal],
    dispatch_totals: dict[str, Decimal],
    sale_totals: dict[str, Decimal],
    avg_prices: dict[str, Decimal],
    global_price: Decimal,
) -> tuple[list[InventoryTypeRow], Decimal, Decimal]:
    type_labels = {code: label for code, label in EggType.choices}
    type_labels.update({code: label for code, label in SaleProductType.choices})
    rows: list[InventoryTypeRow] = []
    inventory_cost_total = ZERO
    dispatch_cost_total = ZERO
    for type_code in ORDERED_EGG_TYPES:
        label = type_labels.get(type_code, type_code.upper())
        classified = classification_totals.get(type_code, ZERO)
        dispatched = dispatch_totals.get(type_code, ZERO)
        sold = sale_totals.get(type_code, ZERO)
        inventory_gap = classified - dispatched
        dispatch_gap = dispatched - sold
        avg_price = avg_prices.get(type_code, global_price)
        inventory_cost = inventory_gap * avg_price if avg_price else ZERO
        dispatch_cost = dispatch_gap * avg_price if avg_price else ZERO
        inventory_cost_total += inventory_cost
        dispatch_cost_total += dispatch_cost
        rows.append(
            InventoryTypeRow(
                type_code=type_code,
                label=label,
                classified=classified,
                dispatched=dispatched,
                sold=sold,
                inventory_gap=inventory_gap,
                dispatch_gap=dispatch_gap,
                average_price=avg_price,
                inventory_cost=inventory_cost,
                dispatch_gap_cost=dispatch_cost,
            )
        )
    return rows, inventory_cost_total, dispatch_cost_total


def _build_price_rows(avg_prices: dict[str, Decimal]) -> list[dict[str, Any]]:
    if not avg_prices:
        return []
    type_labels = {code: label for code, label in EggType.choices}
    type_labels.update({code: label for code, label in SaleProductType.choices})
    rows: list[dict[str, Any]] = []
    for type_code in ORDERED_EGG_TYPES:
        price = avg_prices.get(type_code)
        if price is None or price <= ZERO:
            continue
        rows.append(
            {
                "type": type_code,
                "label": type_labels.get(type_code, type_code.upper()),
                "price": price,
            }
        )
    return rows


def _build_insights(
    *,
    production_gap: Decimal,
    production_gap_cost: Decimal,
    inventory_gap: Decimal,
    inventory_cost: Decimal,
    dispatch_gap: Decimal,
    dispatch_cost: Decimal,
) -> list[InsightCard]:
    insights: list[InsightCard] = []
    if production_gap != ZERO:
        tone = "amber" if production_gap > ZERO else "emerald"
        insights.append(
            InsightCard(
                title="Clasificación pendiente",
                description="Cartones reportados que aún no llegan a inventario clasificado.",
                delta=production_gap,
                cost=production_gap_cost,
                tone=tone,
            )
        )
    if inventory_gap != ZERO:
        tone = "sky" if inventory_gap > ZERO else "emerald"
        insights.append(
            InsightCard(
                title="Inventario clasificado vs. despachos",
                description="Saldo disponible o déficit frente a lo efectivamente despachado.",
                delta=inventory_gap,
                cost=inventory_cost,
                tone=tone,
            )
        )
    if dispatch_gap != ZERO:
        tone = "rose" if dispatch_gap > ZERO else "emerald"
        insights.append(
            InsightCard(
                title="Despachos vs. ventas",
                description="Cartones despachados que aún no están facturados o viceversa.",
                delta=dispatch_gap,
                cost=dispatch_cost,
                tone=tone,
            )
        )
    return insights


def _pluralize(value: int, label: str) -> str:
    suffix = "s" if value != 1 else ""
    return f"{value} {label}{suffix}"

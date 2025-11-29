from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Iterable, Mapping

from django.db.models import (
    DateField,
    DecimalField,
    F,
    OuterRef,
    Q,
    Subquery,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce, Greatest

from administration.models import Sale, SaleItem, SalePayment, SaleProductType
from production.models import (
    BirdBatch,
    EggClassificationBatch,
    EggClassificationEntry,
    EggDispatchItem,
    EggType,
    ProductionRecord,
)
from production.services.egg_classification import ORDERED_EGG_TYPES


DEFAULT_RANGE_DAYS = 30
PRICE_EQUALITY_TOLERANCE = Decimal("0.02")  # ±2 % window treated as par price
PRICE_HISTORY_TYPES = [
    SaleProductType.JUMBO,
    SaleProductType.TRIPLE_A,
    SaleProductType.DOUBLE_A,
    SaleProductType.SINGLE_A,
    SaleProductType.B,
    SaleProductType.C,
]


@dataclass(frozen=True)
class KeyMetricsResult:
    metrics: dict[str, Any]
    charts: dict[str, Any]


def build_key_metrics(start_date: date, end_date: date) -> KeyMetricsResult:
    """Compose the aggregated payload for the executive dashboard."""
    if start_date > end_date:
        start_date, end_date = end_date, start_date

    sales = list(_build_sales_queryset(start_date, end_date))
    sale_ids = [sale.pk for sale in sales]
    sale_items = SaleItem.objects.filter(
        sale_id__in=sale_ids,
        sale__status__in=(Sale.Status.CONFIRMED, Sale.Status.PAID),
    )

    metrics: dict[str, Any] = {}
    metrics["sales_overview"] = _sales_overview(sales)
    metrics["top_customers"] = _rank_top_customers(sales)
    avg_price_rows = _average_prices_by_type(sale_items)
    metrics["average_product_prices"] = avg_price_rows
    metrics["units_summary"] = _units_summary(sale_items)
    metrics["price_history_series"] = _price_history_series(sale_items, avg_price_rows)
    metrics["price_positioning"] = _price_positioning(sale_items, avg_price_rows)
    metrics["overdue_customers"] = _overdue_customers(sales)
    metrics["payment_speed"] = _payment_speed(sales)
    metrics["receivables_overview"] = _build_receivables_overview(
        metrics["overdue_customers"],
        metrics["payment_speed"],
    )
    metrics["dispatch_vs_sales"] = _dispatch_vs_sales(start_date, end_date)
    metrics["production_losses"] = _production_vs_classification(start_date, end_date)
    metrics["type_d_ratios"] = _type_d_ratios(start_date, end_date)
    metrics["mortality_ratios"] = _mortality_ratios(start_date, end_date)

    charts = _build_chart_sources(metrics)
    return KeyMetricsResult(metrics=metrics, charts=charts)


def _build_sales_queryset(start_date: date, end_date: date):
    decimal_field = DecimalField(max_digits=14, decimal_places=2)
    zero_value = Value(Decimal("0.00"), output_field=decimal_field)
    subtotal_subquery = (
        SaleItem.objects.filter(sale_id=OuterRef("pk"))
        .values("sale_id")
        .annotate(total=Sum("subtotal"))
        .values("total")
    )
    payments_subquery = (
        SalePayment.objects.filter(sale_id=OuterRef("pk"))
        .values("sale_id")
        .annotate(total=Sum("amount"))
        .values("total")
    )
    last_payment_subquery = (
        SalePayment.objects.filter(sale_id=OuterRef("pk"))
        .order_by("-date", "-id")
        .values("date")[:1]
    )
    queryset = (
        Sale.objects.filter(date__range=(start_date, end_date))
        .exclude(status=Sale.Status.DRAFT)
        .annotate(
            annotated_subtotal=Coalesce(Subquery(subtotal_subquery, output_field=decimal_field), zero_value),
            annotated_payments_total=Coalesce(Subquery(payments_subquery, output_field=decimal_field), zero_value),
        )
        .annotate(
            annotated_total_amount=Greatest(F("annotated_subtotal") - F("discount_amount"), zero_value),
        )
        .annotate(
            annotated_balance_due=Greatest(F("annotated_total_amount") - F("annotated_payments_total"), zero_value),
            annotated_last_payment=Subquery(last_payment_subquery, output_field=DateField()),
        )
        .select_related("customer")
    )
    return queryset


def _rank_top_customers(sales: Iterable[Sale]) -> list[dict[str, Any]]:
    customer_totals: dict[int, dict[str, Any]] = {}
    overall_sales = Decimal("0.00")
    for sale in sales:
        customer = getattr(sale, "customer", None)
        if not customer:
            continue
        bucket = customer_totals.setdefault(
            customer.pk,
            {
                "name": customer.name,
                "total_amount": Decimal("0.00"),
                "balance_due": Decimal("0.00"),
                "orders": 0,
            },
        )
        net_total = getattr(sale, "annotated_total_amount", Decimal("0.00")) or Decimal("0.00")
        balance = getattr(sale, "annotated_balance_due", Decimal("0.00")) or Decimal("0.00")
        bucket["total_amount"] += net_total
        bucket["balance_due"] += balance
        bucket["orders"] += 1
        overall_sales += net_total

    rows: list[dict[str, Any]] = []
    if overall_sales <= Decimal("0.00"):
        return rows

    for payload in customer_totals.values():
        orders = payload["orders"] or 1
        payload["average_ticket"] = payload["total_amount"] / Decimal(orders)
        payload["share"] = float((payload["total_amount"] / overall_sales) * Decimal("100"))
        rows.append(payload)

    rows.sort(key=lambda row: row["total_amount"], reverse=True)
    return rows[:10]


def _sales_overview(sales: Iterable[Sale]) -> dict[str, Any]:
    total_revenue = Decimal("0.00")
    open_balance = Decimal("0.00")
    total_invoices = 0
    paid_invoices = 0
    for sale in sales:
        net_total = getattr(sale, "annotated_total_amount", Decimal("0.00")) or Decimal("0.00")
        balance = getattr(sale, "annotated_balance_due", Decimal("0.00")) or Decimal("0.00")
        total_revenue += net_total
        open_balance += balance
        total_invoices += 1
        if balance <= Decimal("0.01"):
            paid_invoices += 1
    average_ticket = (total_revenue / Decimal(total_invoices)) if total_invoices else Decimal("0.00")
    collection_rate = ((total_revenue - open_balance) / total_revenue * Decimal("100")) if total_revenue > 0 else Decimal("0.00")
    paid_ratio = (Decimal(paid_invoices) / Decimal(total_invoices) * Decimal("100")) if total_invoices else Decimal("0.00")
    return {
        "total_revenue": total_revenue,
        "open_balance": open_balance,
        "average_ticket": average_ticket,
        "collection_rate": float(collection_rate),
        "paid_ratio": float(paid_ratio),
        "invoice_count": total_invoices,
    }


def _units_summary(sale_items) -> dict[str, Decimal]:
    egg_types = [
        SaleProductType.JUMBO,
        SaleProductType.TRIPLE_A,
        SaleProductType.DOUBLE_A,
        SaleProductType.SINGLE_A,
        SaleProductType.B,
        SaleProductType.C,
        SaleProductType.D,
    ]
    aggregates = (
        sale_items.values("product_type")
        .annotate(total_qty=Sum("quantity"))
    )
    totals: dict[str, Decimal] = {}
    for row in aggregates:
        product_type = row["product_type"]
        qty = row["total_qty"] or Decimal("0.00")
        if product_type:
            totals[product_type] = totals.get(product_type, Decimal("0.00")) + qty
    egg_cartons = sum(totals.get(code, Decimal("0.00")) for code in egg_types)
    return {
        "total_cartons": egg_cartons,
        "hens": totals.get(SaleProductType.HEN, Decimal("0.00")),
        "hen_manure": totals.get(SaleProductType.HEN_MANURE, Decimal("0.00")),
    }


def _average_prices_by_type(sale_items):
    label_map = dict(SaleProductType.choices)
    aggregates = (
        sale_items.values("product_type")
        .annotate(total_qty=Sum("quantity"), total_revenue=Sum("subtotal"))
        .order_by("-total_qty")
    )
    rows: list[dict[str, Any]] = []
    for entry in aggregates:
        qty = entry["total_qty"] or Decimal("0.00")
        revenue = entry["total_revenue"] or Decimal("0.00")
        avg_price = Decimal("0.00")
        if qty > Decimal("0.00"):
            avg_price = revenue / qty
        rows.append(
            {
                "type": entry["product_type"],
                "label": label_map.get(entry["product_type"], entry["product_type"]),
                "avg_price": avg_price,
                "total_qty": qty,
            }
        )
    type_priority = {
        SaleProductType.JUMBO: 0,
        SaleProductType.TRIPLE_A: 1,
        SaleProductType.DOUBLE_A: 2,
        SaleProductType.SINGLE_A: 3,
        SaleProductType.B: 4,
        SaleProductType.C: 5,
    }
    rows.sort(key=lambda row: type_priority.get(row["type"], 99))
    total_qty = sum((row["total_qty"] for row in rows), Decimal("0.00"))
    if total_qty > Decimal("0.00"):
        for row in rows:
            row["share_percent"] = (row["total_qty"] / total_qty) * Decimal("100")
    else:
        for row in rows:
            row["share_percent"] = Decimal("0.00")
    return rows


def _price_history_series(sale_items, avg_price_rows: list[dict[str, Any]]):
    del avg_price_rows  # not needed after enforcing explicit order
    type_priority = PRICE_HISTORY_TYPES
    aggregates = (
        sale_items.filter(product_type__in=type_priority)
        .values("product_type", "sale__date")
        .annotate(total_qty=Sum("quantity"), total_revenue=Sum("subtotal"))
        .order_by("sale__date")
    )
    label_map = dict(SaleProductType.choices)
    series_map: dict[str, dict[str, Any]] = {}
    for row in aggregates:
        code = row["product_type"]
        bucket = series_map.setdefault(
            code,
            {"type": code, "label": label_map.get(code, code), "points": []},
        )
        qty = row["total_qty"] or Decimal("0.00")
        revenue = row["total_revenue"] or Decimal("0.00")
        avg_price = Decimal("0.00")
        if qty > Decimal("0.00"):
            avg_price = revenue / qty
        bucket["points"].append(
            {
                "date": row["sale__date"],
                "avg_price": avg_price,
            }
        )
    ordered_series: list[dict[str, Any]] = []
    for product_type in type_priority:
        bucket = series_map.get(product_type)
        if bucket:
            ordered_series.append(bucket)
    return ordered_series


def _price_positioning(sale_items, avg_price_rows: list[dict[str, Any]]):
    label_map = dict(SaleProductType.choices)
    reference_prices = {row["type"]: row["avg_price"] for row in avg_price_rows if row["avg_price"] > 0}
    if not reference_prices:
        return {"totals": {"below": 0, "within": 0, "above": 0}, "segments": [], "details": [], "type_meta": []}

    tracked_types = [
        SaleProductType.JUMBO,
        SaleProductType.TRIPLE_A,
        SaleProductType.DOUBLE_A,
        SaleProductType.SINGLE_A,
        SaleProductType.B,
        SaleProductType.C,
        SaleProductType.HEN,
        SaleProductType.HEN_MANURE,
    ]

    custom_labels = {
        SaleProductType.HEN: "Gallinas",
        SaleProductType.HEN_MANURE: "Gallinaza",
    }
    type_meta: list[dict[str, Any]] = [
        {
            "type": code,
            "label": custom_labels.get(code, label_map.get(code, code)),
            "reference_avg": reference_prices.get(code),
        }
        for code in tracked_types
    ]

    tracked_type_set = {meta["type"] for meta in type_meta}
    customer_mix: dict[int, dict[str, Any]] = {}
    aggregates = (
        sale_items.filter(product_type__in=tracked_type_set)
        .values("sale__customer_id", "sale__customer__name", "product_type")
        .annotate(total_qty=Sum("quantity"), total_revenue=Sum("subtotal"))
    )
    for row in aggregates:
        customer_id = row["sale__customer_id"]
        if not customer_id:
            continue
        qty = row["total_qty"] or Decimal("0.00")
        revenue = row["total_revenue"] or Decimal("0.00")
        if qty <= Decimal("0.00"):
            continue
        product_type = row["product_type"]
        ref_price = reference_prices.get(product_type)
        payload = customer_mix.setdefault(
            customer_id,
            {
                "name": row["sale__customer__name"],
                "actual": Decimal("0.00"),
                "expected": Decimal("0.00"),
                "type_breakdown": {},
            },
        )
        actual_avg = revenue / qty if qty > Decimal("0.00") else Decimal("0.00")
        payload["type_breakdown"][product_type] = {
            "actual_avg": actual_avg,
            "reference_avg": ref_price,
            "qty": qty,
        }
        if ref_price:
            payload["actual"] += revenue
            payload["expected"] += ref_price * qty

    buckets = {
        "below": {"count": 0, "customers": []},
        "within": {"count": 0, "customers": []},
        "above": {"count": 0, "customers": []},
    }
    details: list[dict[str, Any]] = []
    bucket_order = {"below": 0, "within": 1, "above": 2}
    tone_map = {
        "below": "text-rose-600",
        "within": "text-slate-700",
        "above": "text-emerald-600",
    }
    label_map_segment = {
        "below": "Menor precio",
        "within": "En línea",
        "above": "Mayor precio",
    }
    for payload in customer_mix.values():
        expected = payload["expected"]
        actual = payload["actual"]
        if expected <= Decimal("0.00"):
            continue
        ratio = (actual - expected) / expected
        if ratio <= -PRICE_EQUALITY_TOLERANCE:
            bucket = "below"
        elif ratio >= PRICE_EQUALITY_TOLERANCE:
            bucket = "above"
        else:
            bucket = "within"
        buckets[bucket]["count"] += 1
        buckets[bucket]["customers"].append(payload["name"])
        type_info_map = payload.get("type_breakdown", {})
        type_values: list[dict[str, Any]] = []
        for meta in type_meta:
            detail = type_info_map.get(meta["type"])
            type_values.append(
                {
                    "type": meta["type"],
                    "value": detail["actual_avg"] if detail else None,
                }
            )
        details.append(
            {
                "name": payload["name"],
                "segment": bucket,
                "segment_label": label_map_segment[bucket],
                "expected_total": expected,
                "actual_total": actual,
                "delta_percentage": float(ratio * Decimal("100")),
                "delta_value": actual - expected,
                "type_values": type_values,
                "tone_class": tone_map[bucket],
                "order": bucket_order[bucket],
            }
        )

    details.sort(key=lambda row: (row["order"], row["delta_percentage"]))

    baseline_values = [
        {"type": meta["type"], "value": meta["reference_avg"]}
        for meta in type_meta
    ]
    details.insert(
        0,
        {
            "name": "",
            "segment": "baseline",
            "segment_label": "Promedio",
            "expected_total": None,
            "actual_total": None,
            "delta_percentage": None,
            "delta_value": None,
            "type_values": baseline_values,
            "tone_class": "text-amber-900",
            "order": -1,
            "is_baseline": True,
        },
    )

    segments = []
    total_clients = sum(bucket["count"] for bucket in buckets.values()) or 1
    for key, payload in buckets.items():
        segments.append(
            {
                "segment": key,
                "count": payload["count"],
                "share": float((payload["count"] / total_clients) * 100),
                "sample": payload["customers"][:5],
            }
        )
    return {
        "totals": {key: payload["count"] for key, payload in buckets.items()},
        "segments": segments,
        "details": details,
        "type_meta": type_meta,
    }


def _overdue_customers(sales: Iterable[Sale]) -> list[dict[str, Any]]:
    today = date.today()
    rows: dict[int, dict[str, Any]] = {}
    for sale in sales:
        due_date = getattr(sale, "payment_due_date", None)
        balance = getattr(sale, "annotated_balance_due", Decimal("0.00")) or Decimal("0.00")
        if not due_date or due_date >= today or balance <= Decimal("0.00"):
            continue
        customer = getattr(sale, "customer", None)
        if not customer:
            continue
        payload = rows.setdefault(
            customer.pk,
            {
                "customer_id": customer.pk,
                "name": customer.name,
                "overdue_balance": Decimal("0.00"),
                "oldest_due_days": 0,
                "open_invoices": 0,
            },
        )
        payload["overdue_balance"] += balance
        payload["open_invoices"] += 1
        days_overdue = (today - due_date).days
        payload["oldest_due_days"] = max(payload["oldest_due_days"], days_overdue)
    ordered = sorted(rows.values(), key=lambda row: row["overdue_balance"], reverse=True)
    return ordered[:8]


def _payment_speed(sales: Iterable[Sale]) -> dict[str, Any]:
    total_days = 0
    total_sales = 0
    client_stats: dict[int, dict[str, Any]] = {}
    for sale in sales:
        balance = getattr(sale, "annotated_balance_due", Decimal("0.00")) or Decimal("0.00")
        last_payment = getattr(sale, "annotated_last_payment", None)
        if balance > Decimal("0.01") or not last_payment:
            continue
        days_to_pay = max((last_payment - sale.date).days, 0)
        total_days += days_to_pay
        total_sales += 1
        customer = getattr(sale, "customer", None)
        if not customer:
            continue
        payload = client_stats.setdefault(
            customer.pk,
            {"name": customer.name, "days": 0, "count": 0},
        )
        payload["days"] += days_to_pay
        payload["count"] += 1

    slow_clients = []
    per_customer: dict[int, dict[str, Any]] = {}
    for customer_id, payload in client_stats.items():
        if payload["count"] <= 0:
            continue
        avg_days = payload["days"] / payload["count"]
        slow_clients.append(
            {
                "name": payload["name"],
                "avg_days": avg_days,
                "paid_sales": payload["count"],
            }
        )
        per_customer[customer_id] = {
            "name": payload["name"],
            "avg_days": avg_days,
            "paid_sales": payload["count"],
        }
    slow_clients.sort(key=lambda row: row["avg_days"], reverse=True)

    avg_days_global = (total_days / total_sales) if total_sales else 0
    return {
        "global_avg_days": avg_days_global,
        "samples": total_sales,
        "slow_clients": slow_clients[:5],
        "per_customer": per_customer,
    }


def _dispatch_vs_sales(start_date: date, end_date: date) -> dict[str, Any]:
    dispatch_rows = (
        EggDispatchItem.objects.filter(dispatch__date__range=(start_date, end_date))
        .values("egg_type")
        .annotate(total_cartons=Sum("cartons"))
    )
    dispatch_totals = {row["egg_type"]: row["total_cartons"] or Decimal("0.00") for row in dispatch_rows}
    sale_rows = (
        SaleItem.objects.filter(
            sale__date__range=(start_date, end_date),
            sale__status__in=(Sale.Status.CONFIRMED, Sale.Status.PAID),
        )
        .values("product_type")
        .annotate(total_qty=Sum("quantity"))
    )
    sale_totals = {row["product_type"]: row["total_qty"] or Decimal("0.00") for row in sale_rows}
    label_map = dict(SaleProductType.choices)
    per_type = []
    total_dispatched = Decimal("0.00")
    total_sold = Decimal("0.00")
    for egg_type in ORDERED_EGG_TYPES:
        dispatched = dispatch_totals.get(egg_type, Decimal("0.00"))
        sold = sale_totals.get(egg_type, Decimal("0.00"))
        if dispatched <= Decimal("0.00") and sold <= Decimal("0.00"):
            continue
        total_dispatched += dispatched
        total_sold += sold
        per_type.append(
            {
                "type": egg_type,
                "label": label_map.get(egg_type, egg_type),
                "dispatched": dispatched,
                "sold": sold,
                "gap": dispatched - sold,
            }
        )
    return {
        "per_type": per_type,
        "total_dispatched": total_dispatched,
        "total_sold": total_sold,
        "gap": total_dispatched - total_sold,
    }


def _production_vs_classification(start_date: date, end_date: date) -> dict[str, Any]:
    batch_rows = (
        EggClassificationBatch.objects.filter(production_record__date__range=(start_date, end_date))
        .aggregate(
            reported_cartons=Coalesce(Sum("reported_cartons"), Decimal("0.00")),
            received_cartons=Coalesce(Sum("received_cartons"), Decimal("0.00")),
        )
    )
    classified_rows = (
        EggClassificationEntry.objects.filter(batch__production_record__date__range=(start_date, end_date))
        .aggregate(total_classified=Coalesce(Sum("cartons"), Decimal("0.00")))
    )
    reported = batch_rows.get("reported_cartons") or Decimal("0.00")
    classified = classified_rows.get("total_classified") or Decimal("0.00")
    gap = reported - classified
    return {
        "reported_cartons": reported,
        "classified_cartons": classified,
        "gap": gap,
    }


def _type_d_ratios(start_date: date, end_date: date) -> dict[str, Any]:
    aggregates = (
        EggClassificationEntry.objects.filter(batch__production_record__date__range=(start_date, end_date))
        .values("batch__bird_batch_id", "batch__bird_batch__farm__name")
        .annotate(
            total_cartons=Sum("cartons"),
            d_cartons=Sum("cartons", filter=Q(egg_type=EggType.D)),
        )
    )
    rows = []
    total_d = Decimal("0.00")
    total_all = Decimal("0.00")
    batch_ids: set[int] = set()
    for row in aggregates:
        total = row["total_cartons"] or Decimal("0.00")
        d_value = row["d_cartons"] or Decimal("0.00")
        if total <= Decimal("0.00"):
            continue
        total_all += total
        total_d += d_value
        ratio = float((d_value / total) * Decimal("100"))
        batch_id = row["batch__bird_batch_id"] or 0
        batch_ids.add(batch_id)
        farm_name = row["batch__bird_batch__farm__name"] or "Sin granja"
        rows.append(
            {
                "batch_id": batch_id,
                "label": f"Lote #{batch_id} · {farm_name}",
                "ratio": ratio,
                "d_cartons": d_value,
                "total_cartons": total,
            }
        )
    house_labels = _build_batch_house_labels(batch_ids)
    for entry in rows:
        batch_label = house_labels.get(entry["batch_id"])
        if batch_label:
            farm_name = entry["label"].split("·")[-1].strip()
            entry["label"] = f"{batch_label} · {farm_name}"
    rows.sort(key=lambda row: row["ratio"], reverse=True)
    global_ratio = float(((total_d / total_all) * Decimal("100")) if total_all > 0 else 0)
    return {"global_ratio": global_ratio, "per_batch": rows[:5]}


def _mortality_ratios(start_date: date, end_date: date) -> list[dict[str, Any]]:
    aggregates = (
        ProductionRecord.objects.filter(date__range=(start_date, end_date))
        .values("bird_batch_id", "bird_batch__farm__name", "bird_batch__initial_quantity")
        .annotate(total_mortality=Sum("mortality"))
    )
    rows = []
    batch_ids: set[int] = set()
    for row in aggregates:
        initial_quantity = row["bird_batch__initial_quantity"] or 0
        total_mortality = row["total_mortality"] or 0
        if initial_quantity <= 0 or total_mortality <= 0:
            continue
        ratio = (Decimal(total_mortality) / Decimal(initial_quantity)) * Decimal("100")
        batch_id = row["bird_batch_id"] or 0
        batch_ids.add(batch_id)
        farm_name = row["bird_batch__farm__name"] or "Sin granja"
        rows.append(
            {
                "batch_id": batch_id,
                "label": f"Lote #{batch_id} · {farm_name}",
                "ratio": float(ratio),
                "mortality": total_mortality,
            }
        )
    house_labels = _build_batch_house_labels(batch_ids)
    for entry in rows:
        batch_label = house_labels.get(entry["batch_id"])
        if batch_label:
            farm_name = entry["label"].split("·")[-1].strip()
            entry["label"] = f"{batch_label} · {farm_name}"
    rows.sort(key=lambda row: row["ratio"], reverse=True)
    return rows[:5]


def _build_chart_sources(metrics: Mapping[str, Any]) -> dict[str, Any]:
    charts: dict[str, Any] = {}
    price_history = []
    for series in metrics.get("price_history_series", []):
        price_history.append(
            {
                "label": series["label"],
                "data": [
                    {"x": point["date"].isoformat(), "y": float(point["avg_price"])}
                    for point in series["points"]
                    if point["date"]
                ],
            }
        )
    charts["priceHistory"] = price_history

    dispatch_metrics = metrics.get("dispatch_vs_sales", {})
    per_type = dispatch_metrics.get("per_type", [])
    charts["dispatchVsSales"] = {
        "labels": [row["label"] for row in per_type],
        "dispatched": [float(row["dispatched"]) for row in per_type],
        "sold": [float(row["sold"]) for row in per_type],
    }

    production_metrics = metrics.get("production_losses", {})
    charts["productionVsClassification"] = {
        "labels": ["Reportado", "Clasificado"],
        "data": [
            float(production_metrics.get("reported_cartons", 0)),
            float(production_metrics.get("classified_cartons", 0)),
        ],
    }

    type_d_metrics = metrics.get("type_d_ratios", {})
    charts["typeDRatios"] = {
        "labels": [row["label"] for row in type_d_metrics.get("per_batch", [])],
        "values": [row["ratio"] for row in type_d_metrics.get("per_batch", [])],
        "global": type_d_metrics.get("global_ratio", 0),
    }

    mortality_rows = metrics.get("mortality_ratios", [])
    charts["mortalityRatios"] = {
        "labels": [row["label"] for row in mortality_rows],
        "values": [row["ratio"] for row in mortality_rows],
    }
    return charts


def _build_batch_house_labels(batch_ids: Iterable[int]) -> dict[int, str]:
    ids = [batch_id for batch_id in batch_ids if batch_id]
    if not ids:
        return {}
    result: dict[int, str] = {}
    batches = (
        BirdBatch.objects.filter(pk__in=ids)
        .prefetch_related("allocations__room__chicken_house")
    )
    for batch in batches:
        names: list[str] = []
        seen: set[str] = set()
        for allocation in batch.allocations.all():
            room = allocation.room
            house = getattr(room, "chicken_house", None)
            if not house:
                continue
            if house.name in seen:
                continue
            seen.add(house.name)
            names.append(house.name)
        names.sort()
        if names:
            result[batch.pk] = " / ".join(names)
    return result


def _build_receivables_overview(
    overdue_rows: Iterable[dict[str, Any]],
    payment_speed: Mapping[str, Any],
) -> dict[str, Any]:
    customer_stats = payment_speed.get("per_customer") or {}
    rows: list[dict[str, Any]] = []
    for row in overdue_rows:
        customer_id = row.get("customer_id")
        stats = customer_stats.get(customer_id) if isinstance(customer_stats, dict) else None
        rows.append(
            {
                **row,
                "avg_days": stats.get("avg_days") if stats else None,
                "paid_sales": stats.get("paid_sales") if stats else 0,
            }
        )
    return {
        "rows": rows,
        "global_avg_days": payment_speed.get("global_avg_days") or 0,
        "samples": payment_speed.get("samples") or 0,
    }

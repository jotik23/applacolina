from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Iterable

from django.db import models
from django.db.models import Avg, ExpressionWrapper, F, Sum
from django.db.models.functions import Cast, Coalesce

from administration.models import PurchaseRequest, Sale, SaleItem
from production.models import (
    BirdBatch,
    BirdBatchRoomAllocation,
    EggClassificationBatch,
    EggClassificationEntry,
    EggType,
    ProductionRecord,
)

CARTON_DIVISOR = Decimal("30")
CURRENCY_QUANTIZER = Decimal("0.01")

DATE_FIELD = models.DateField()
DECIMAL_FIELD = models.DecimalField(max_digits=18, decimal_places=2)


@dataclass(frozen=True)
class IncomeEntry:
    egg_type: str
    label: str
    cartons: Decimal
    avg_price: Decimal
    estimated_income: Decimal
    price_source: str


@dataclass(frozen=True)
class IncomeProjection:
    entries: list[IncomeEntry]
    total_cartons: Decimal
    total_income: Decimal
    global_average_price: Decimal

    @property
    def effective_average_price(self) -> Decimal:
        if self.total_cartons <= Decimal("0"):
            return Decimal("0")
        return _quantize_currency(self.total_income / self.total_cartons)


@dataclass(frozen=True)
class ExpenseEntry:
    category_id: int | None
    category_name: str
    direct: Decimal
    farm_allocation: Decimal
    company_allocation: Decimal

    @property
    def total(self) -> Decimal:
        return self.direct + self.farm_allocation + self.company_allocation


@dataclass(frozen=True)
class ExpenseBreakdown:
    entries: list[ExpenseEntry]
    total_direct: Decimal
    total_farm: Decimal
    total_company: Decimal

    @property
    def total(self) -> Decimal:
        return self.total_direct + self.total_farm + self.total_company


@dataclass(frozen=True)
class BirdBatchClosureResult:
    batch: BirdBatch
    start_date: date
    end_date: date
    range_days: int
    production: dict[str, Any]
    classification: dict[str, Any]
    income: IncomeProjection
    expenses: ExpenseBreakdown
    inventory_alignment: dict[str, Decimal]


def build_bird_batch_closure_report(*, batch_id: int, start_date: date, end_date: date) -> BirdBatchClosureResult:
    if start_date > end_date:
        raise ValueError("El rango de fechas es inválido.")

    batch = (
        BirdBatch.objects.select_related("farm", "breed")
        .prefetch_related("allocations__room__chicken_house")
        .get(pk=batch_id)
    )
    range_days = (end_date - start_date).days + 1
    production_summary = _build_production_summary(batch=batch, start_date=start_date, end_date=end_date, range_days=range_days)
    classification_summary = _build_classification_summary(batch=batch, start_date=start_date, end_date=end_date)
    income_projection = _build_income_projection(
        start_date=start_date,
        end_date=end_date,
        classification_summary=classification_summary,
    )
    expenses = _build_expense_breakdown(batch=batch, start_date=start_date, end_date=end_date)
    inventory_alignment = _build_inventory_alignment(production_summary, classification_summary)
    return BirdBatchClosureResult(
        batch=batch,
        start_date=start_date,
        end_date=end_date,
        range_days=range_days,
        production=production_summary,
        classification=classification_summary,
        income=income_projection,
        expenses=expenses,
        inventory_alignment=inventory_alignment,
    )


def _build_production_summary(*, batch: BirdBatch, start_date: date, end_date: date, range_days: int) -> dict[str, Any]:
    aggregates = (
        ProductionRecord.objects.filter(bird_batch=batch, date__range=(start_date, end_date))
        .aggregate(
            total_production=Coalesce(Sum("production"), Decimal("0"), output_field=DECIMAL_FIELD),
            total_consumption=Coalesce(Sum("consumption"), Decimal("0"), output_field=DECIMAL_FIELD),
            total_mortality=Coalesce(Sum("mortality"), 0, output_field=models.IntegerField()),
            total_discard=Coalesce(Sum("discard"), 0, output_field=models.IntegerField()),
            avg_weight=Avg("average_egg_weight"),
        )
    )
    record_count = ProductionRecord.objects.filter(bird_batch=batch, date__range=(start_date, end_date)).count()
    produced_eggs = Decimal(aggregates.get("total_production") or 0)
    produced_cartons = _quantize_decimal(produced_eggs / CARTON_DIVISOR) if produced_eggs > 0 else Decimal("0.00")
    consumption = Decimal(aggregates.get("total_consumption") or 0)
    mortality = int(aggregates.get("total_mortality") or 0)
    discard = int(aggregates.get("total_discard") or 0)
    avg_weight = aggregates.get("avg_weight")
    avg_weight_value = _quantize_decimal(Decimal(avg_weight)) if avg_weight is not None else None

    initial_quantity = Decimal(batch.initial_quantity or 0)
    avg_daily_per_bird = Decimal("0")
    feed_per_bird = Decimal("0")
    if initial_quantity > 0 and range_days > 0:
        avg_daily_per_bird = _quantize_decimal(produced_eggs / initial_quantity / Decimal(range_days))
        feed_per_bird = _quantize_decimal(consumption / initial_quantity)

    cumulative_totals = (
        ProductionRecord.objects.filter(bird_batch=batch, date__lte=end_date)
        .aggregate(
            cumulative_mortality=Coalesce(Sum("mortality"), 0, output_field=models.IntegerField()),
            cumulative_discard=Coalesce(Sum("discard"), 0, output_field=models.IntegerField()),
        )
    )
    cumulative_mortality = int(cumulative_totals.get("cumulative_mortality") or 0)
    cumulative_discard = int(cumulative_totals.get("cumulative_discard") or 0)
    population_estimate = max(batch.initial_quantity - cumulative_mortality, 0)

    age_start_days = max((start_date - batch.birth_date).days, 0)
    age_end_days = max((end_date - batch.birth_date).days, 0)
    age_start_weeks = _quantize_value(Decimal(age_start_days) / Decimal("7"), Decimal("0.1")) if age_start_days else Decimal("0.0")
    age_end_weeks = _quantize_value(Decimal(age_end_days) / Decimal("7"), Decimal("0.1")) if age_end_days else Decimal("0.0")

    return {
        "records_count": record_count,
        "produced_eggs": produced_eggs,
        "produced_cartons": produced_cartons,
        "feed_consumption": consumption,
        "mortality": mortality,
        "discard": discard,
        "avg_egg_weight": avg_weight_value,
        "avg_daily_egg_per_bird": avg_daily_per_bird,
        "feed_per_bird": feed_per_bird,
        "cumulative_mortality": cumulative_mortality,
        "cumulative_discard": cumulative_discard,
        "population_estimate": population_estimate,
        "age_start_days": age_start_days,
        "age_end_days": age_end_days,
        "age_start_weeks": age_start_weeks,
        "age_end_weeks": age_end_weeks,
    }


def _build_classification_summary(*, batch: BirdBatch, start_date: date, end_date: date) -> dict[str, Any]:
    batch_stats = (
        EggClassificationBatch.objects.filter(bird_batch=batch, production_record__date__range=(start_date, end_date))
        .aggregate(
            reported=Coalesce(Sum("reported_cartons"), Decimal("0"), output_field=DECIMAL_FIELD),
            received=Coalesce(Sum("received_cartons"), Decimal("0"), output_field=DECIMAL_FIELD),
        )
    )
    entry_rows = (
        EggClassificationEntry.objects.filter(
            batch__bird_batch=batch,
            batch__production_record__date__range=(start_date, end_date),
        )
        .values("egg_type")
        .annotate(total_cartons=Coalesce(Sum("cartons"), Decimal("0"), output_field=DECIMAL_FIELD))
    )
    per_type_lookup = {row["egg_type"]: Decimal(row["total_cartons"] or 0) for row in entry_rows}
    type_labels = dict(EggType.choices)
    per_type: list[dict[str, Any]] = []
    classified_total = Decimal("0")
    for egg_type, label in EggType.choices:
        cartons = per_type_lookup.get(egg_type, Decimal("0"))
        if cartons <= Decimal("0"):
            continue
        classified_total += cartons
        per_type.append(
            {
                "egg_type": egg_type,
                "label": label,
                "cartons": cartons,
            }
        )
    reported = Decimal(batch_stats.get("reported") or 0)
    received = Decimal(batch_stats.get("received") or 0)
    pending = max(reported - classified_total, Decimal("0"))
    return {
        "per_type": per_type,
        "reported_cartons": reported,
        "received_cartons": received,
        "classified_cartons": classified_total,
        "pending_cartons": pending,
    }


def _build_income_projection(*, start_date: date, end_date: date, classification_summary: dict[str, Any]) -> IncomeProjection:
    sale_rows = (
        SaleItem.objects.filter(
            sale__date__range=(start_date, end_date),
            sale__status__in=(Sale.Status.CONFIRMED, Sale.Status.PAID),
        )
        .values("product_type")
        .annotate(
            total_quantity=Coalesce(Sum("quantity"), Decimal("0"), output_field=DECIMAL_FIELD),
            weighted_total=Coalesce(
                Sum(
                    ExpressionWrapper(F("quantity") * F("unit_price"), output_field=DECIMAL_FIELD)
                ),
                Decimal("0"),
                output_field=DECIMAL_FIELD,
            ),
        )
    )
    price_map: dict[str, Decimal] = {}
    total_amount = Decimal("0")
    total_quantity = Decimal("0")
    for row in sale_rows:
        quantity = Decimal(row["total_quantity"] or 0)
        weighted_total = Decimal(row["weighted_total"] or 0)
        if quantity <= Decimal("0"):
            continue
        avg_price = weighted_total / quantity
        price_map[row["product_type"]] = _quantize_currency(avg_price)
        total_amount += weighted_total
        total_quantity += quantity
    global_avg_price = _quantize_currency(total_amount / total_quantity) if total_quantity > 0 else Decimal("0")

    entries: list[IncomeEntry] = []
    classified_rows: Iterable[dict[str, Any]] = classification_summary.get("per_type", [])
    total_cartons = Decimal("0")
    total_income = Decimal("0")
    for row in classified_rows:
        cartons = Decimal(row.get("cartons") or 0)
        if cartons <= Decimal("0"):
            continue
        egg_type = row["egg_type"]
        avg_price = price_map.get(egg_type) or global_avg_price
        estimated_income = _quantize_currency(cartons * avg_price)
        total_cartons += cartons
        total_income += estimated_income
        entries.append(
            IncomeEntry(
                egg_type=egg_type,
                label=row.get("label") or egg_type,
                cartons=cartons,
                avg_price=avg_price,
                estimated_income=estimated_income,
                price_source="type" if egg_type in price_map else "global",
            )
        )

    return IncomeProjection(
        entries=entries,
        total_cartons=total_cartons,
        total_income=total_income,
        global_average_price=global_avg_price,
    )


def _build_expense_breakdown(*, batch: BirdBatch, start_date: date, end_date: date) -> ExpenseBreakdown:
    analysis_date = Coalesce(
        "payment_date",
        "invoice_date",
        "purchase_date",
        "order_date",
        Cast("created_at", output_field=DATE_FIELD),
        output_field=DATE_FIELD,
    )
    executed_total = Coalesce(
        "invoice_total",
        "payment_amount",
        "estimated_total",
        output_field=DECIMAL_FIELD,
    )
    queryset = (
        PurchaseRequest.objects.select_related("expense_type", "expense_type__parent_category")
        .prefetch_related("items__scope_farm", "items__scope_chicken_house__farm")
        .annotate(analysis_date=analysis_date, executed_total=executed_total)
        .annotate(
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
        .filter(
            analysis_date__gte=start_date,
            analysis_date__lte=end_date,
            status__in=PurchaseRequest.POST_PAYMENT_STATUSES,
        )
    )

    house_ids = _resolve_batch_house_ids(batch)
    farm_id = batch.farm_id
    farm_share, company_share = _resolve_proration_shares(batch)

    category_entries: dict[int | None, dict[str, Decimal]] = {}
    total_direct = Decimal("0")
    total_farm = Decimal("0")
    total_company = Decimal("0")

    for purchase in queryset:
        amount = Decimal(purchase.executed_total or 0)
        if amount <= Decimal("0"):
            continue
        scope_kind = _resolve_purchase_scope(purchase, batch_id=batch.id, farm_id=farm_id, house_ids=house_ids)
        if scope_kind == "skip":
            continue
        if scope_kind == "farm" and farm_share <= Decimal("0"):
            continue
        if scope_kind == "company" and company_share <= Decimal("0"):
            continue

        entry = category_entries.setdefault(
            purchase.parent_category_id,
            {
                "name": purchase.parent_category_name or "Sin categoría",
                "direct": Decimal("0"),
                "farm": Decimal("0"),
                "company": Decimal("0"),
            },
        )
        if scope_kind == "direct":
            entry["direct"] += amount
            total_direct += amount
        elif scope_kind == "farm":
            allocation = _quantize_currency(amount * farm_share)
            entry["farm"] += allocation
            total_farm += allocation
        else:
            allocation = _quantize_currency(amount * company_share)
            entry["company"] += allocation
            total_company += allocation

    entries = [
        ExpenseEntry(
            category_id=category_id,
            category_name=data["name"],
            direct=_quantize_currency(data["direct"]),
            farm_allocation=_quantize_currency(data["farm"]),
            company_allocation=_quantize_currency(data["company"]),
        )
        for category_id, data in category_entries.items()
    ]
    entries.sort(key=lambda item: item.total, reverse=True)
    return ExpenseBreakdown(
        entries=entries,
        total_direct=_quantize_currency(total_direct),
        total_farm=_quantize_currency(total_farm),
        total_company=_quantize_currency(total_company),
    )


def _build_inventory_alignment(production_summary: dict[str, Any], classification_summary: dict[str, Any]) -> dict[str, Decimal]:
    produced_cartons = Decimal(production_summary.get("produced_cartons") or 0)
    reported_cartons = Decimal(classification_summary.get("reported_cartons") or 0)
    classified_cartons = Decimal(classification_summary.get("classified_cartons") or 0)
    difference_vs_reported = produced_cartons - reported_cartons
    pending_classification = Decimal(classification_summary.get("pending_cartons") or 0)
    return {
        "produced_cartons": produced_cartons,
        "reported_cartons": reported_cartons,
        "classified_cartons": classified_cartons,
        "difference_vs_reported": difference_vs_reported,
        "pending_classification": pending_classification,
    }


def _resolve_proration_shares(batch: BirdBatch) -> tuple[Decimal, Decimal]:
    farm_batches = BirdBatch.objects.filter(farm=batch.farm).filter(
        models.Q(status=BirdBatch.Status.ACTIVE) | models.Q(pk=batch.pk)
    )
    farm_total = farm_batches.aggregate(total=models.Sum("initial_quantity"))
    farm_denominator = Decimal(farm_total.get("total") or 0)
    company_batches = BirdBatch.objects.filter(
        models.Q(status=BirdBatch.Status.ACTIVE) | models.Q(pk=batch.pk)
    )
    company_total = company_batches.aggregate(total=models.Sum("initial_quantity"))
    company_denominator = Decimal(company_total.get("total") or 0)
    numerator = Decimal(batch.initial_quantity or 0)
    farm_share = numerator / farm_denominator if farm_denominator > 0 else Decimal("0")
    company_share = numerator / company_denominator if company_denominator > 0 else Decimal("0")
    return farm_share, company_share


def _resolve_batch_house_ids(batch: BirdBatch) -> set[int]:
    prefetched = getattr(batch, "_prefetched_objects_cache", {})
    allocations = prefetched.get("allocations")
    if allocations is None:
        allocations = list(
            BirdBatchRoomAllocation.objects.filter(bird_batch=batch)
            .select_related("room__chicken_house")
        )
    house_ids: set[int] = set()
    for allocation in allocations:
        room = getattr(allocation, "room", None)
        if not room:
            continue
        chicken_house_id = getattr(room, "chicken_house_id", None)
        if chicken_house_id:
            house_ids.add(chicken_house_id)
    return house_ids


def _resolve_purchase_scope(
    purchase: PurchaseRequest,
    *,
    batch_id: int,
    farm_id: int,
    house_ids: set[int],
) -> str:
    if _matches_batch_code(purchase.scope_batch_code, batch_id):
        return "direct"

    items = getattr(purchase, "_cached_scope_items", None)
    if items is None:
        items = list(purchase.items.all())
        purchase._cached_scope_items = items

    has_company = False
    has_farm_scope = False
    for item in items:
        scope_area = item.scope_area or PurchaseRequest.AreaScope.COMPANY
        if scope_area == PurchaseRequest.AreaScope.CHICKEN_HOUSE:
            house_id = item.scope_chicken_house_id
            if house_id and house_id in house_ids:
                return "direct"
            if item.scope_chicken_house and item.scope_chicken_house.farm_id == farm_id:
                has_farm_scope = True
        elif scope_area == PurchaseRequest.AreaScope.FARM:
            if item.scope_farm_id == farm_id:
                return "farm"
        else:
            has_company = True

    if has_farm_scope:
        return "farm"
    if has_company:
        return "company"
    return "skip"


def _matches_batch_code(scope_batch_code: str | None, batch_id: int) -> bool:
    if not scope_batch_code:
        return False
    match = re.search(r"(\d+)", scope_batch_code)
    if not match:
        return False
    try:
        code = int(match.group(1))
    except (TypeError, ValueError):
        return False
    return code == batch_id


def _quantize_currency(value: Decimal) -> Decimal:
    return _quantize_value(value, CURRENCY_QUANTIZER)


def _quantize_decimal(value: Decimal) -> Decimal:
    return _quantize_value(value, Decimal("0.01"))


def _quantize_value(value: Decimal, quantizer: Decimal) -> Decimal:
    return value.quantize(quantizer, rounding=ROUND_HALF_UP)

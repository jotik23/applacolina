from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from typing import Dict, Mapping, Optional

from django.db import transaction
from django.db.models import Case, IntegerField, Max, Sum, Value, When
from django.utils import timezone

from production.models import (
    EggClassificationBatch,
    EggClassificationEntry,
    EggType,
    ProductionRecord,
)


@dataclass(frozen=True)
class PendingBatch:
    id: int
    production_date: date
    lot_label: str
    farm_name: str
    reported_cartons: Decimal
    received_cartons: Optional[Decimal]
    pending_cartons: Decimal
    status: str
    status_label: str
    difference: Decimal


@dataclass(frozen=True)
class InventoryRow:
    egg_type: str
    label: str
    cartons: Decimal
    last_classified_at: Optional[date]


@dataclass(frozen=True)
class InventoryFlowRecord:
    batch_id: int
    farm_name: str
    lot_label: str
    produced_cartons: Decimal
    confirmed_cartons: Optional[Decimal]
    classified_cartons: Decimal
    type_breakdown: Dict[str, Decimal]
    classifier_name: Optional[str]
    collector_name: Optional[str]
    delta_receipt: Decimal
    delta_inventory: Decimal


@dataclass(frozen=True)
class InventoryFlow:
    day: date
    produced_cartons: Decimal
    confirmed_cartons: Decimal
    classified_cartons: Decimal
    type_breakdown: Dict[str, Decimal]
    records: list[InventoryFlowRecord]
    delta_receipt: Decimal
    delta_inventory: Decimal


EGGS_PER_CARTON = Decimal("30")
CARTON_QUANTUM = Decimal("0.01")


def _to_decimal(value: Optional[Decimal]) -> Optional[Decimal]:
    if value in (None, "", "—", "..."):
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(value)
    except (InvalidOperation, ArithmeticError, TypeError, ValueError):
        return None


def eggs_to_cartons(value: Optional[Decimal]) -> Optional[Decimal]:
    """Convert an egg count into its carton representation."""
    decimal_value = _to_decimal(value)
    if decimal_value is None:
        return None
    if decimal_value == 0:
        return Decimal("0")
    cartons = decimal_value / EGGS_PER_CARTON
    return cartons.quantize(CARTON_QUANTUM, rounding=ROUND_HALF_UP)


def eggs_to_cartons_or_zero(value: Optional[Decimal]) -> Decimal:
    converted = eggs_to_cartons(value)
    return converted if converted is not None else Decimal("0")


def ensure_batch_for_record(record: ProductionRecord) -> EggClassificationBatch:
    """Guarantee there is a classification batch tied to the production record."""
    batch, _ = EggClassificationBatch.objects.get_or_create(
        production_record=record,
        defaults={
            "bird_batch": record.bird_batch,
            "reported_cartons": record.production,
        },
    )

    dirty_fields: list[str] = []
    if batch.bird_batch_id != record.bird_batch_id:
        batch.bird_batch = record.bird_batch
        dirty_fields.append("bird_batch")

    if batch.reported_cartons != record.production:
        batch.reported_cartons = record.production
        dirty_fields.append("reported_cartons")

    if dirty_fields:
        dirty_fields.append("updated_at")
        batch.save(update_fields=dirty_fields)
    return batch


def confirm_batch_receipt(
    *,
    batch: EggClassificationBatch,
    received_cartons: Decimal,
    notes: str,
    actor_id: Optional[int],
) -> EggClassificationBatch:
    """Persist the amount delivered to the classification team."""
    batch.received_cartons = received_cartons
    batch.notes = notes
    if batch.status != EggClassificationBatch.Status.CLASSIFIED:
        batch.status = EggClassificationBatch.Status.CONFIRMED
    batch.confirmed_at = timezone.now()
    batch.confirmed_by_id = actor_id
    batch.save(
        update_fields=[
            "received_cartons",
            "notes",
            "status",
            "confirmed_at",
            "confirmed_by",
            "updated_at",
        ]
    )
    return batch


def record_classification_results(
    *,
    batch: EggClassificationBatch,
    entries: Mapping[str, Decimal],
    actor_id: Optional[int],
) -> EggClassificationBatch:
    """Replace the classification distribution for the batch."""
    timestamp = timezone.now()
    with transaction.atomic():
        batch.classification_entries.all().delete()

        entry_models: list[EggClassificationEntry] = []
        for egg_type, cartons in entries.items():
            if cartons <= 0:
                continue
            entry_models.append(
                EggClassificationEntry(
                    batch=batch,
                    egg_type=egg_type,
                    cartons=cartons,
                )
            )
        EggClassificationEntry.objects.bulk_create(entry_models)

        batch.classified_at = timestamp
        batch.classified_by_id = actor_id
        batch.status = EggClassificationBatch.Status.CLASSIFIED
        batch.save(
            update_fields=[
                "classified_at",
                "classified_by",
                "status",
                "updated_at",
            ]
        )
    return batch


def build_pending_batches(limit: int = 50) -> list[PendingBatch]:
    qs = (
        EggClassificationBatch.objects.select_related(
            "bird_batch",
            "bird_batch__farm",
        )
        .annotate(
            status_order=Case(
                When(status=EggClassificationBatch.Status.PENDING, then=Value(0)),
                When(status=EggClassificationBatch.Status.CONFIRMED, then=Value(1)),
                default=Value(2),
                output_field=IntegerField(),
            )
        )
        .order_by("status_order", "production_record__date", "bird_batch__id")
    )

    batches: list[PendingBatch] = []
    for batch in qs[:limit]:
        classified_total = getattr(batch, "classified_total_cache", None)
        if classified_total is not None:
            batch._classified_total_cache = Decimal(classified_total)
        pending = batch.pending_cartons
        if pending < 0:
            pending = Decimal("0")
        pending_cartons = eggs_to_cartons_or_zero(pending)
        reported_cartons = eggs_to_cartons_or_zero(batch.reported_cartons)
        if reported_cartons <= Decimal("0"):
            continue
        if pending_cartons <= Decimal("1"):
            continue
        received_cartons = eggs_to_cartons(batch.received_cartons)
        difference = eggs_to_cartons(batch.received_difference)
        batches.append(
            PendingBatch(
                id=batch.pk,
                production_date=batch.production_date,
                lot_label=str(batch.bird_batch),
                farm_name=batch.farm.name,
                reported_cartons=reported_cartons,
                received_cartons=received_cartons,
                pending_cartons=pending_cartons,
                status=batch.status,
                status_label=batch.get_status_display(),
                difference=difference if difference is not None else Decimal("0"),
            )
        )
    return batches


def summarize_classified_inventory() -> list[InventoryRow]:
    aggregates = (
        EggClassificationEntry.objects.values("egg_type")
        .annotate(
            total=Sum("cartons"),
            last_classified=Max("batch__classified_at"),
        )
        .order_by("egg_type")
    )
    aggregate_map = {aggregate["egg_type"]: aggregate for aggregate in aggregates}

    ordered_types = [
        EggType.JUMBO,
        EggType.TRIPLE_A,
        EggType.DOUBLE_A,
        EggType.SINGLE_A,
        EggType.B,
        EggType.C,
        EggType.D,
    ]
    label_map = dict(EggType.choices)

    rows: list[InventoryRow] = []
    for egg_type in ordered_types:
        aggregate = aggregate_map.get(egg_type)
        total = Decimal(aggregate["total"] or 0) if aggregate else Decimal("0")
        total_cartons = eggs_to_cartons_or_zero(total)
        classified_at = aggregate["last_classified"] if aggregate else None
        rows.append(
            InventoryRow(
                egg_type=egg_type,
                label=label_map.get(egg_type, egg_type),
                cartons=total_cartons,
                last_classified_at=classified_at.date() if classified_at else None,
            )
        )
    return rows


def build_inventory_flow(days: int = 7) -> list[InventoryFlow]:
    if days <= 0:
        return []

    end_date = timezone.localdate()
    start_date = end_date - timedelta(days=days - 1)
    return build_inventory_flow_range(start_date=start_date, end_date=end_date)


def build_inventory_flow_range(
    *,
    start_date: date,
    end_date: date,
    farm_id: Optional[int] = None,
) -> list[InventoryFlow]:
    if start_date > end_date:
        return []
    return _build_inventory_flow(start_date=start_date, end_date=end_date, farm_id=farm_id)


def _build_inventory_flow(
    *,
    start_date: date,
    end_date: date,
    farm_id: Optional[int],
) -> list[InventoryFlow]:
    def _display_name(user) -> Optional[str]:
        if not user:
            return None
        full_name = user.get_full_name()
        if full_name:
            return full_name
        short_name = getattr(user, "get_short_name", None)
        if callable(short_name):
            short_value = short_name()
            if short_value:
                return short_value
        username = getattr(user, "username", None)
        return username or str(user)

    batches_qs = EggClassificationBatch.objects.filter(
        production_record__date__gte=start_date,
        production_record__date__lte=end_date,
    )
    if farm_id:
        batches_qs = batches_qs.filter(bird_batch__farm_id=farm_id)

    batches = (
        batches_qs.select_related(
            "bird_batch",
            "bird_batch__farm",
            "production_record",
            "production_record__created_by",
            "classified_by",
        )
        .prefetch_related("classification_entries")
        .order_by("production_record__date", "bird_batch__farm__name", "bird_batch__id")
    )

    records_by_day: dict[date, list[InventoryFlowRecord]] = defaultdict(list)
    for batch in batches:
        entries = list(batch.classification_entries.all())
        record_breakdown: Dict[str, Decimal] = defaultdict(Decimal)
        classified_total_eggs = Decimal("0")
        for entry in entries:
            qty = Decimal(entry.cartons or 0)
            record_breakdown[entry.egg_type] += eggs_to_cartons_or_zero(qty)
            classified_total_eggs += qty

        confirmed_value = Decimal(batch.received_cartons) if batch.received_cartons is not None else None
        confirmed_for_math = confirmed_value if confirmed_value is not None else Decimal("0")
        inventory_source = confirmed_value if confirmed_value is not None else Decimal(batch.reported_cartons)
        produced_cartons = eggs_to_cartons_or_zero(batch.reported_cartons)
        confirmed_cartons = eggs_to_cartons(confirmed_value)
        classified_cartons = eggs_to_cartons_or_zero(classified_total_eggs)
        delta_receipt = eggs_to_cartons_or_zero(Decimal(batch.reported_cartons) - confirmed_for_math)
        delta_inventory = eggs_to_cartons_or_zero(inventory_source - classified_total_eggs)
        record = InventoryFlowRecord(
            batch_id=batch.pk,
            farm_name=batch.bird_batch.farm.name,
            lot_label=str(batch.bird_batch),
            produced_cartons=produced_cartons,
            confirmed_cartons=confirmed_cartons,
            classified_cartons=classified_cartons,
            type_breakdown=dict(record_breakdown),
            classifier_name=_display_name(batch.classified_by),
            collector_name=_display_name(getattr(batch.production_record, "created_by", None)),
            delta_receipt=delta_receipt,
            delta_inventory=delta_inventory,
        )
        records_by_day[batch.production_date].append(record)

    total_days = (end_date - start_date).days + 1
    flows: list[InventoryFlow] = []
    for step in range(total_days):
        day = start_date + timedelta(days=step)
        day_records = sorted(
            records_by_day.get(day, []),
            key=lambda record: (record.farm_name, record.lot_label),
        )
        produced = sum((record.produced_cartons for record in day_records), Decimal("0"))
        confirmed = sum(((record.confirmed_cartons or Decimal("0")) for record in day_records), Decimal("0"))
        classified = sum((record.classified_cartons for record in day_records), Decimal("0"))
        delta_receipt = sum((record.delta_receipt for record in day_records), Decimal("0"))
        delta_inventory = sum((record.delta_inventory for record in day_records), Decimal("0"))
        day_breakdown: Dict[str, Decimal] = defaultdict(Decimal)
        for record in day_records:
            for egg_type, qty in record.type_breakdown.items():
                day_breakdown[egg_type] += qty
        flows.append(
            InventoryFlow(
                day=day,
                produced_cartons=produced,
                confirmed_cartons=confirmed,
                classified_cartons=classified,
                type_breakdown=dict(day_breakdown),
                records=day_records,
                delta_receipt=delta_receipt,
                delta_inventory=delta_inventory,
            )
        )
    return flows


def compute_unclassified_total() -> Decimal:
    pending_total = Decimal("0")
    qs = (
        EggClassificationBatch.objects.annotate(
            classified_total=Sum("classification_entries__cartons"),
        )
        .values("reported_cartons", "received_cartons", "classified_total")
    )
    for row in qs:
        reported = Decimal(row["reported_cartons"] or 0)
        received = row["received_cartons"]
        received_value = Decimal(received) if received is not None else None
        classified = Decimal(row["classified_total"] or 0)
        source = received_value if received_value is not None else reported
        balance = source - classified
        if balance > 0:
            pending_total += eggs_to_cartons_or_zero(balance)
    return pending_total

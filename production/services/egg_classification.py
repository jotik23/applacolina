from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Dict, Mapping, Optional

from django.db import transaction
from django.db.models import Case, IntegerField, Max, Sum, Value, When
from django.db.models.functions import TruncDate
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
class InventoryFlow:
    day: date
    produced_cartons: Decimal
    confirmed_cartons: Decimal
    classified_cartons: Decimal
    type_breakdown: Dict[str, Decimal]


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
        batches.append(
            PendingBatch(
                id=batch.pk,
                production_date=batch.production_date,
                lot_label=str(batch.bird_batch),
                farm_name=batch.farm.name,
                reported_cartons=Decimal(batch.reported_cartons),
                received_cartons=Decimal(batch.received_cartons) if batch.received_cartons is not None else None,
                pending_cartons=pending,
                status=batch.status,
                status_label=batch.get_status_display(),
                difference=batch.received_difference,
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
    rows: list[InventoryRow] = []
    for aggregate in aggregates:
        egg_type = aggregate["egg_type"]
        label = dict(EggType.choices).get(egg_type, egg_type)
        total = aggregate["total"] or Decimal("0")
        classified_at = aggregate["last_classified"]
        rows.append(
            InventoryRow(
                egg_type=egg_type,
                label=label,
                cartons=total,
                last_classified_at=classified_at.date() if classified_at else None,
            )
        )
    return rows


def build_inventory_flow(days: int = 7) -> list[InventoryFlow]:
    if days <= 0:
        return []

    today = timezone.localdate()
    start_date = today - timedelta(days=days - 1)

    produced_map: dict[date, Dict[str, Decimal]] = {}
    production_qs = (
        EggClassificationBatch.objects.filter(production_record__date__gte=start_date)
        .values("production_record__date")
        .annotate(
            produced=Sum("reported_cartons"),
            confirmed=Sum("received_cartons"),
        )
    )
    for row in production_qs:
        day = row["production_record__date"]
        produced_map[day] = {
            "produced": row["produced"] or Decimal("0"),
            "confirmed": row["confirmed"] or Decimal("0"),
        }

    breakdown: dict[date, Dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    classification_totals: dict[date, Decimal] = defaultdict(Decimal)
    classification_qs = (
        EggClassificationEntry.objects.filter(batch__classified_at__isnull=False)
        .annotate(classification_date=TruncDate("batch__classified_at"))
        .filter(classification_date__gte=start_date)
        .values("classification_date", "egg_type")
        .annotate(total=Sum("cartons"))
    )
    for row in classification_qs:
        day = row["classification_date"]
        total = row["total"] or Decimal("0")
        egg_type = row["egg_type"]
        breakdown[day][egg_type] += total
        classification_totals[day] += total

    flows: list[InventoryFlow] = []
    for step in range(days):
        day = start_date + timedelta(days=step)
        produced = produced_map.get(day, {}).get("produced", Decimal("0"))
        confirmed = produced_map.get(day, {}).get("confirmed", Decimal("0"))
        classified = classification_totals.get(day, Decimal("0"))
        flows.append(
            InventoryFlow(
                day=day,
                produced_cartons=produced,
                confirmed_cartons=confirmed,
                classified_cartons=classified,
                type_breakdown=dict(breakdown.get(day, {})),
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
            pending_total += balance
    return pending_total

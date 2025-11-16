from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Mapping, Optional

from django.db import transaction
from django.db.models import Case, IntegerField, Max, Sum, Value, When
from django.utils import timezone

from production.models import (
    EggClassificationBatch,
    EggClassificationSession,
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
    sessions: list["ClassificationSessionRecord"]


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


@dataclass(frozen=True)
class ClassificationSessionRecord:
    id: int
    classified_at: datetime
    classifier_name: Optional[str]
    type_breakdown: Dict[str, Decimal]
    total_cartons: Decimal


@dataclass(frozen=True)
class ClassificationSessionFlowRecord:
    id: int
    batch_id: int
    farm_name: str
    lot_label: str
    production_date: date
    produced_cartons: Decimal
    confirmed_cartons: Optional[Decimal]
    session_cartons: Decimal
    classified_at: datetime
    classifier_name: Optional[str]
    type_breakdown: Dict[str, Decimal]


@dataclass(frozen=True)
class ClassificationSessionDay:
    day: date
    total_cartons: Decimal
    sessions: list[ClassificationSessionFlowRecord]


EGGS_PER_CARTON = Decimal("30")
CARTON_QUANTUM = Decimal("0.01")


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


def eggs_to_cartons(value: Optional[Decimal]) -> Decimal:
    """Normalize egg counts into carton units."""
    if value is None:
        return Decimal("0")
    return (Decimal(value) / EGGS_PER_CARTON).quantize(CARTON_QUANTUM, rounding=ROUND_HALF_UP)


def ensure_batch_for_record(record: ProductionRecord) -> EggClassificationBatch:
    """Guarantee there is a classification batch tied to the production record."""
    reported_cartons = eggs_to_cartons(record.production)
    batch, _ = EggClassificationBatch.objects.get_or_create(
        production_record=record,
        defaults={
            "bird_batch": record.bird_batch,
            "reported_cartons": reported_cartons,
        },
    )

    dirty_fields: list[str] = []
    if batch.bird_batch_id != record.bird_batch_id:
        batch.bird_batch = record.bird_batch
        dirty_fields.append("bird_batch")

    if batch.reported_cartons != reported_cartons:
        batch.reported_cartons = reported_cartons
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
    """Append a new classification session for the batch."""
    timestamp = timezone.now()
    sanitized_entries: list[tuple[str, Decimal]] = []
    for egg_type, cartons in entries.items():
        qty = Decimal(cartons)
        if qty <= 0:
            continue
        sanitized_entries.append((egg_type, qty))

    if not sanitized_entries:
        raise ValueError("No hay cantidades positivas para clasificar.")

    with transaction.atomic():
        session = EggClassificationSession.objects.create(
            batch=batch,
            classified_at=timestamp,
            classified_by_id=actor_id,
        )
        entry_models: list[EggClassificationEntry] = [
            EggClassificationEntry(
                batch=batch,
                session=session,
                egg_type=egg_type,
                cartons=qty,
            )
            for egg_type, qty in sanitized_entries
        ]
        EggClassificationEntry.objects.bulk_create(entry_models)

        aggregates = EggClassificationEntry.objects.filter(batch=batch).aggregate(total=Sum("cartons"))
        total_classified = Decimal(aggregates.get("total") or 0)
        batch._classified_total_cache = total_classified
        batch.classified_at = timestamp
        batch.classified_by_id = actor_id
        source_cartons = (
            Decimal(batch.received_cartons)
            if batch.received_cartons is not None
            else Decimal(batch.reported_cartons)
        )
        if total_classified >= source_cartons:
            batch.status = EggClassificationBatch.Status.CLASSIFIED
        else:
            batch.status = EggClassificationBatch.Status.CONFIRMED
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
        if batch.reported_cartons <= Decimal("0"):
            continue
        if pending <= Decimal("1"):
            continue
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
                difference=Decimal(batch.received_difference),
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
        total_cartons = Decimal(aggregate["total"] or 0) if aggregate else Decimal("0")
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


def build_classification_session_flow_range(
    *,
    start_date: date,
    end_date: date,
    farm_id: Optional[int] = None,
) -> list[ClassificationSessionDay]:
    if start_date > end_date:
        return []
    return _build_classification_session_flow(start_date=start_date, end_date=end_date, farm_id=farm_id)


def _build_inventory_flow(
    *,
    start_date: date,
    end_date: date,
    farm_id: Optional[int],
) -> list[InventoryFlow]:

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
        .prefetch_related("classification_entries", "classification_sessions__entries")
        .order_by("production_record__date", "bird_batch__farm__name", "bird_batch__id")
    )

    records_by_day: dict[date, list[InventoryFlowRecord]] = defaultdict(list)
    for batch in batches:
        entries = list(batch.classification_entries.all())
        record_breakdown: Dict[str, Decimal] = defaultdict(Decimal)
        classified_total = Decimal("0")
        for entry in entries:
            qty = Decimal(entry.cartons or 0)
            record_breakdown[entry.egg_type] += qty
            classified_total += qty

        session_rows: list[ClassificationSessionRecord] = []
        sessions = sorted(batch.classification_sessions.all(), key=lambda item: item.classified_at)
        for session in sessions:
            session_breakdown: Dict[str, Decimal] = defaultdict(Decimal)
            session_total = Decimal("0")
            for entry in session.entries.all():
                qty = Decimal(entry.cartons or 0)
                session_breakdown[entry.egg_type] += qty
                session_total += qty
            session_rows.append(
                ClassificationSessionRecord(
                    id=session.pk,
                    classified_at=session.classified_at,
                    classifier_name=_display_name(session.classified_by),
                    type_breakdown=dict(session_breakdown),
                    total_cartons=session_total,
                )
            )

        confirmed_value = Decimal(batch.received_cartons) if batch.received_cartons is not None else None
        confirmed_for_math = confirmed_value if confirmed_value is not None else Decimal("0")
        inventory_source = confirmed_value if confirmed_value is not None else Decimal(batch.reported_cartons)
        produced_cartons = Decimal(batch.reported_cartons)
        confirmed_cartons = confirmed_value
        classified_cartons = classified_total
        delta_receipt = Decimal(batch.reported_cartons) - confirmed_for_math
        delta_inventory = inventory_source - classified_total
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
            sessions=session_rows,
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


def _build_classification_session_flow(
    *,
    start_date: date,
    end_date: date,
    farm_id: Optional[int],
) -> list[ClassificationSessionDay]:
    sessions_qs = EggClassificationSession.objects.filter(
        classified_at__date__gte=start_date,
        classified_at__date__lte=end_date,
    )
    if farm_id:
        sessions_qs = sessions_qs.filter(batch__bird_batch__farm_id=farm_id)

    sessions = (
        sessions_qs.select_related(
            "batch",
            "batch__bird_batch",
            "batch__bird_batch__farm",
            "batch__production_record",
            "classified_by",
        )
        .prefetch_related("entries")
        .order_by("-classified_at")
    )

    records_by_day: dict[date, list[ClassificationSessionFlowRecord]] = defaultdict(list)
    for session in sessions:
        breakdown: Dict[str, Decimal] = defaultdict(Decimal)
        session_total = Decimal("0")
        for entry in session.entries.all():
            qty = Decimal(entry.cartons or 0)
            breakdown[entry.egg_type] += qty
            session_total += qty

        batch = session.batch
        confirmed_value = (
            Decimal(batch.received_cartons) if batch.received_cartons is not None else None
        )
        local_dt = timezone.localtime(session.classified_at)
        records_by_day[local_dt.date()].append(
            ClassificationSessionFlowRecord(
                id=session.pk,
                batch_id=batch.pk,
                farm_name=batch.bird_batch.farm.name,
                lot_label=str(batch.bird_batch),
                production_date=batch.production_date,
                produced_cartons=Decimal(batch.reported_cartons),
                confirmed_cartons=confirmed_value,
                session_cartons=session_total,
                classified_at=local_dt,
                classifier_name=_display_name(session.classified_by),
                type_breakdown=dict(breakdown),
            )
        )

    days: list[ClassificationSessionDay] = []
    for day in sorted(records_by_day.keys(), reverse=True):
        sessions_for_day = sorted(
            records_by_day[day],
            key=lambda record: record.classified_at,
            reverse=True,
        )
        total = sum((record.session_cartons for record in sessions_for_day), Decimal("0"))
        days.append(
            ClassificationSessionDay(
                day=day,
                total_cartons=total,
                sessions=sessions_for_day,
            )
        )
    return days


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

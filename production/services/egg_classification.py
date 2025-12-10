from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Mapping, Optional

from django.db import transaction
from django.db.models import Case, IntegerField, Max, Prefetch, Sum, Value, When
from django.utils import timezone

from production.models import (
    BirdBatchRoomAllocation,
    EggClassificationBatch,
    EggClassificationSession,
    EggClassificationEntry,
    EggDispatch,
    EggDispatchItem,
    EggType,
    ProductionRecord,
)


@dataclass(frozen=True)
class PendingBatch:
    id: int
    production_date: date
    lot_label: str
    chicken_houses: list[str]
    farm_name: str
    reported_cartons: Decimal
    received_cartons: Optional[Decimal]
    pending_cartons: Decimal
    status: str
    status_label: str
    difference: Decimal
    transport_status: str
    transport_status_label: str
    transport_destination: str
    transport_expected_date: Optional[date]
    transport_confirmed_cartons: Optional[Decimal]


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
    chicken_houses: list[str]
    produced_cartons: Decimal
    confirmed_cartons: Optional[Decimal]
    classified_cartons: Decimal
    type_breakdown: Dict[str, Decimal]
    classifier_name: Optional[str]
    collector_name: Optional[str]
    delta_receipt: Decimal
    delta_inventory: Decimal
    last_classified_at: Optional[datetime]


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
    collector_name: Optional[str]
    type_breakdown: Dict[str, Decimal]
    delta_receipt: Decimal
    inventory_balance: Decimal


@dataclass(frozen=True)
class ClassificationSessionDay:
    day: date
    total_cartons: Decimal
    sessions: list[ClassificationSessionFlowRecord]


@dataclass(frozen=True)
class DispatchFlowRecord:
    id: int
    destination: str
    destination_label: str
    total_cartons: Decimal
    type_breakdown: Dict[str, Decimal]
    seller_name: Optional[str]
    driver_name: Optional[str]


@dataclass(frozen=True)
class DispatchDayFlow:
    day: date
    total_cartons: Decimal
    type_breakdown: Dict[str, Decimal]
    dispatches: list[DispatchFlowRecord]


@dataclass(frozen=True)
class ClassificationTotals:
    overall: dict[str, Decimal]
    by_farm: dict[int, dict[str, Decimal]]


ORDERED_EGG_TYPES = [
    EggType.JUMBO,
    EggType.TRIPLE_A,
    EggType.DOUBLE_A,
    EggType.SINGLE_A,
    EggType.B,
    EggType.C,
    EggType.D,
]

EGGS_PER_CARTON = Decimal("30")
CARTON_QUANTUM = Decimal("0.01")


def _local_day_bounds(target_date: date) -> tuple[datetime, datetime]:
    """Return timezone-aware start/end datetimes for the provided day."""

    local_tz = timezone.get_current_timezone()
    start_naive = datetime.combine(target_date, datetime.min.time())
    end_naive = datetime.combine(target_date + timedelta(days=1), datetime.min.time())
    return (
        timezone.make_aware(start_naive, local_tz),
        timezone.make_aware(end_naive, local_tz),
    )


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


def _collect_chicken_house_names(batch: EggClassificationBatch) -> list[str]:
    """Return the distinct chicken houses where the batch is allocated."""
    allocations = getattr(batch.bird_batch, "allocations", None)
    if allocations is None:
        return []

    names: list[str] = []
    seen: set[str] = set()
    for allocation in batch.bird_batch.allocations.all():
        room = allocation.room
        house = getattr(room, "chicken_house", None)
        if not house:
            continue
        if house.name in seen:
            continue
        seen.add(house.name)
        names.append(house.name)
    names.sort()
    return names


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


def delete_classification_session(*, session: EggClassificationSession) -> EggClassificationBatch:
    """Delete a specific classification iteration and refresh batch aggregates."""
    batch = session.batch
    with transaction.atomic():
        session.delete()
        aggregates = EggClassificationEntry.objects.filter(batch=batch).aggregate(total=Sum("cartons"))
        total_classified = Decimal(aggregates.get("total") or 0)
        batch._classified_total_cache = total_classified
        latest_session = (
            batch.classification_sessions.order_by("-classified_at", "-pk").first()
        )
        if latest_session:
            batch.classified_at = latest_session.classified_at
            batch.classified_by = latest_session.classified_by
        else:
            batch.classified_at = None
            batch.classified_by = None

        if total_classified <= 0:
            batch.status = (
                EggClassificationBatch.Status.CONFIRMED
                if batch.received_cartons is not None
                else EggClassificationBatch.Status.PENDING
            )
        else:
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


def update_classification_session_date(
    *,
    session: EggClassificationSession,
    classified_date: date,
) -> EggClassificationSession:
    """Allow adjusting the classification session date while preserving the time."""

    local_tz = timezone.get_current_timezone()
    original_timestamp = session.classified_at or timezone.now()
    local_dt = timezone.localtime(original_timestamp, timezone=local_tz)
    local_time = local_dt.timetz().replace(tzinfo=None)
    new_local = datetime.combine(classified_date, local_time)
    new_timestamp = timezone.make_aware(new_local, local_tz)

    with transaction.atomic():
        session.classified_at = new_timestamp
        session.save(update_fields=["classified_at", "updated_at"])

        batch = session.batch
        latest_session = batch.classification_sessions.order_by("-classified_at", "-pk").first()
        if latest_session:
            batch.classified_at = latest_session.classified_at
            batch.classified_by = latest_session.classified_by
        else:
            batch.classified_at = None
            batch.classified_by = None
        batch.save(update_fields=["classified_at", "classified_by", "updated_at"])

    return session


def reset_batch_progress(*, batch: EggClassificationBatch) -> EggClassificationBatch:
    """Remove confirmations and sessions so the batch can be reprocessed."""
    with transaction.atomic():
        EggClassificationSession.objects.filter(batch=batch).delete()
        EggClassificationEntry.objects.filter(batch=batch).delete()
        batch._classified_total_cache = Decimal("0")
        batch.received_cartons = Decimal("0")
        batch.notes = ""
        batch.status = EggClassificationBatch.Status.PENDING
        batch.confirmed_at = None
        batch.confirmed_by_id = None
        batch.classified_at = None
        batch.classified_by_id = None
        batch.save(
            update_fields=[
                "received_cartons",
                "notes",
                "status",
                "confirmed_at",
                "confirmed_by",
                "classified_at",
                "classified_by",
                "updated_at",
            ]
        )
    return batch


def build_pending_batches(limit: int = 50) -> list[PendingBatch]:
    qs = (
        EggClassificationBatch.objects.select_related(
            "bird_batch",
            "bird_batch__farm",
            "transport_destination_farm",
        )
        .prefetch_related(
            Prefetch(
                "bird_batch__allocations",
                queryset=BirdBatchRoomAllocation.objects.select_related("room__chicken_house"),
            )
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
        chicken_houses = _collect_chicken_house_names(batch)
        destination_name = (
            batch.transport_destination_farm.name if batch.transport_destination_farm else batch.farm.name
        )
        batches.append(
            PendingBatch(
                id=batch.pk,
                production_date=batch.production_date,
                lot_label=str(batch.bird_batch),
                chicken_houses=chicken_houses,
                farm_name=batch.farm.name,
                reported_cartons=Decimal(batch.reported_cartons),
                received_cartons=Decimal(batch.received_cartons) if batch.received_cartons is not None else None,
                pending_cartons=pending,
                status=batch.status,
                status_label=batch.get_status_display(),
                difference=Decimal(batch.received_difference),
                transport_status=batch.transport_status,
                transport_status_label=batch.get_transport_status_display(),
                transport_destination=destination_name,
                transport_expected_date=batch.transport_expected_date,
                transport_confirmed_cartons=Decimal(batch.transport_confirmed_cartons)
                if batch.transport_confirmed_cartons is not None
                else None,
            )
        )
    return batches


def get_inventory_balance_by_type(*, exclude_dispatch_id: Optional[int] = None) -> dict[str, Decimal]:
    """Return available classified inventory per egg type after dispatches."""

    classification_totals = (
        EggClassificationEntry.objects.values("egg_type")
        .annotate(total=Sum("cartons"))
        .order_by("egg_type")
    )
    classification_map: dict[str, Decimal] = {
        row["egg_type"]: Decimal(row["total"] or 0) for row in classification_totals if row["egg_type"]
    }

    dispatch_qs = EggDispatchItem.objects.all()
    if exclude_dispatch_id:
        dispatch_qs = dispatch_qs.exclude(dispatch_id=exclude_dispatch_id)
    dispatch_totals = dispatch_qs.values("egg_type").annotate(total=Sum("cartons"))
    dispatch_map: dict[str, Decimal] = {
        row["egg_type"]: Decimal(row["total"] or 0) for row in dispatch_totals if row["egg_type"]
    }

    balances: dict[str, Decimal] = {}
    for egg_type in ORDERED_EGG_TYPES:
        classified_total = classification_map.get(egg_type, Decimal("0"))
        dispatched_total = dispatch_map.get(egg_type, Decimal("0"))
        balances[egg_type] = classified_total - dispatched_total
    return balances


def get_inventory_balance_until(*, until: date, farm_id: Optional[int] = None) -> dict[str, Decimal]:
    """Return classified inventory by type up to a given day, optionally filtered by farm."""

    _, upper_bound = _local_day_bounds(until)
    entry_queryset = EggClassificationEntry.objects.filter(session__classified_at__lt=upper_bound)
    if farm_id:
        entry_queryset = entry_queryset.filter(batch__bird_batch__farm_id=farm_id)
    classification_totals = (
        entry_queryset.values("egg_type")
        .annotate(total=Sum("cartons"))
        .order_by("egg_type")
    )
    classification_map: dict[str, Decimal] = {
        row["egg_type"]: Decimal(row["total"] or 0) for row in classification_totals if row["egg_type"]
    }

    dispatch_queryset = EggDispatchItem.objects.filter(dispatch__date__lte=until)
    dispatch_totals = dispatch_queryset.values("egg_type").annotate(total=Sum("cartons"))
    dispatch_map: dict[str, Decimal] = {
        row["egg_type"]: Decimal(row["total"] or 0) for row in dispatch_totals if row["egg_type"]
    }

    balances: dict[str, Decimal] = {}
    for egg_type in ORDERED_EGG_TYPES:
        classified_total = classification_map.get(egg_type, Decimal("0"))
        dispatched_total = dispatch_map.get(egg_type, Decimal("0"))
        balances[egg_type] = classified_total - dispatched_total
    return balances


def get_classification_totals_between(
    *,
    start_date: date,
    end_date: date,
    farm_id: Optional[int] = None,
) -> ClassificationTotals:
    """Return overall and per-farm classified totals for sessions within the window."""

    if start_date > end_date:
        return ClassificationTotals(overall={}, by_farm={})

    lower_bound, _ = _local_day_bounds(start_date)
    _, final_upper_bound = _local_day_bounds(end_date)
    entry_queryset = EggClassificationEntry.objects.filter(
        session__classified_at__gte=lower_bound,
        session__classified_at__lt=final_upper_bound,
    )
    if farm_id:
        entry_queryset = entry_queryset.filter(batch__bird_batch__farm_id=farm_id)
    aggregates = (
        entry_queryset.values("egg_type", "batch__bird_batch__farm_id")
        .annotate(total=Sum("cartons"))
        .order_by("egg_type")
    )
    overall_totals: dict[str, Decimal] = defaultdict(Decimal)
    breakdown: dict[int, dict[str, Decimal]] = {}
    for row in aggregates:
        egg_type = row["egg_type"]
        farm_id_value = row["batch__bird_batch__farm_id"]
        if not egg_type:
            continue
        qty = Decimal(row["total"] or 0)
        overall_totals[egg_type] += qty
        if farm_id_value is None:
            continue
        farm_totals = breakdown.setdefault(farm_id_value, {})
        farm_totals[egg_type] = farm_totals.get(egg_type, Decimal("0")) + qty
    return ClassificationTotals(overall=dict(overall_totals), by_farm=breakdown)


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

    label_map = dict(EggType.choices)
    balances = get_inventory_balance_by_type()

    rows: list[InventoryRow] = []
    for egg_type in ORDERED_EGG_TYPES:
        aggregate = aggregate_map.get(egg_type)
        total_cartons = balances.get(egg_type, Decimal("0"))
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


def build_dispatch_flow_range(*, start_date: date, end_date: date) -> list[DispatchDayFlow]:
    if start_date > end_date:
        return []
    dispatches = list(
        EggDispatch.objects.filter(date__gte=start_date, date__lte=end_date)
        .select_related("driver", "seller")
        .prefetch_related("items")
        .order_by("date", "-id")
    )
    records_by_day: dict[date, list[DispatchFlowRecord]] = defaultdict(list)
    for dispatch in dispatches:
        daily_breakdown: Dict[str, Decimal] = defaultdict(Decimal)
        for item in dispatch.items.all():
            qty = Decimal(item.cartons or 0)
            daily_breakdown[item.egg_type] += qty
        records_by_day[dispatch.date].append(
            DispatchFlowRecord(
                id=dispatch.pk,
                destination=dispatch.destination,
                destination_label=dispatch.get_destination_display(),
                total_cartons=Decimal(dispatch.total_cartons or 0),
                type_breakdown=dict(daily_breakdown),
                seller_name=dispatch.seller_name,
                driver_name=dispatch.driver_name,
            )
        )

    day_flows: list[DispatchDayFlow] = []
    for day, dispatch_records in records_by_day.items():
        day_type_totals: Dict[str, Decimal] = defaultdict(Decimal)
        total_cartons = Decimal("0")
        for record in dispatch_records:
            total_cartons += record.total_cartons
            for egg_type, qty in record.type_breakdown.items():
                day_type_totals[egg_type] += qty
        dispatch_records.sort(key=lambda record: record.id, reverse=True)
        day_flows.append(
            DispatchDayFlow(
                day=day,
                total_cartons=total_cartons,
                type_breakdown=dict(day_type_totals),
                dispatches=dispatch_records,
            )
        )

    day_flows.sort(key=lambda flow: flow.day)
    return day_flows


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

    allocation_prefetch = Prefetch(
        "bird_batch__allocations",
        queryset=BirdBatchRoomAllocation.objects.select_related("room__chicken_house"),
    )

    batches = (
        batches_qs.select_related(
            "bird_batch",
            "bird_batch__farm",
            "production_record",
            "production_record__created_by",
            "classified_by",
        )
        .prefetch_related(
            "classification_entries",
            "classification_sessions__entries",
            allocation_prefetch,
        )
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

        last_classified_at = timezone.localtime(batch.classified_at) if batch.classified_at else None

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
            chicken_houses=_collect_chicken_house_names(batch),
            produced_cartons=produced_cartons,
            confirmed_cartons=confirmed_cartons,
            classified_cartons=classified_cartons,
            type_breakdown=dict(record_breakdown),
            classifier_name=_display_name(batch.classified_by),
            collector_name=_display_name(getattr(batch.production_record, "created_by", None)),
            delta_receipt=delta_receipt,
            delta_inventory=delta_inventory,
            last_classified_at=last_classified_at,
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

    sessions = list(
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

    payload_by_session: dict[int, tuple[Dict[str, Decimal], Decimal, Decimal, Decimal]] = {}
    cumulative_classified: dict[int, Decimal] = defaultdict(Decimal)
    for session in sorted(sessions, key=lambda record: record.classified_at):
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
        confirmed_for_math = confirmed_value if confirmed_value is not None else Decimal("0")
        inventory_source = confirmed_value if confirmed_value is not None else Decimal(batch.reported_cartons)
        consumed = cumulative_classified[batch.pk] + session_total
        remaining_inventory = inventory_source - consumed
        cumulative_classified[batch.pk] = consumed
        delta_receipt = Decimal(batch.reported_cartons) - confirmed_for_math

        payload_by_session[session.pk] = (
            dict(breakdown),
            session_total,
            remaining_inventory,
            delta_receipt,
        )

    records_by_day: dict[date, list[ClassificationSessionFlowRecord]] = defaultdict(list)
    for session in sessions:
        batch = session.batch
        local_dt = timezone.localtime(session.classified_at)
        confirmed_value = (
            Decimal(batch.received_cartons) if batch.received_cartons is not None else None
        )
        payload = payload_by_session.get(session.pk)
        if payload:
            breakdown, session_total, inventory_balance, delta_receipt = payload
        else:
            breakdown = {}
            session_total = Decimal("0")
            inventory_balance = Decimal("0")
            delta_receipt = Decimal("0")
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
                collector_name=_display_name(getattr(batch.production_record, "created_by", None)),
                type_breakdown=breakdown,
                delta_receipt=delta_receipt,
                inventory_balance=inventory_balance,
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

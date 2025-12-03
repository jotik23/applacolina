from __future__ import annotations

import hashlib
from collections import OrderedDict
from datetime import date, timedelta
from decimal import Decimal
from typing import Iterable, Mapping, Optional

from django.contrib.auth import get_user_model
from django.utils.translation import gettext as _

from production.models import (
    BirdBatch,
    BirdBatchRoomAllocation,
    ProductionRecord,
    ProductionRoomRecord,
    Room,
    WeightSampleSession,
)
from task_manager.mini_app.features.weight_registry import (
    DECIMAL_QUANTIZE,
    WeightLocation,
    WeightMetrics,
    WeightRegistry,
    WeightSessionSnapshot,
    WeightSessionSummary,
)

User = get_user_model()


def build_batch_weight_registry(
    *,
    batch: BirdBatch,
    target_date: date,
    actor: Optional[User] = None,
    min_sample_size: int = 30,
    uniformity_tolerance_percent: int = 10,
) -> Optional[WeightRegistry]:
    """Return a weight registry snapshot for the selected batch and day."""

    allocations = (
        BirdBatchRoomAllocation.objects.filter(bird_batch=batch)
        .select_related("room__chicken_house__farm")
        .order_by("room__chicken_house__name", "room__name")
    )
    rooms = _collect_rooms_from_allocations(allocations)
    if not rooms:
        return None

    room_ids = [room.pk for room in rooms]
    birds_map = _aggregate_birds_per_room(allocations)
    locations = _build_locations(rooms, birds_map)

    production_record = (
        ProductionRecord.objects.filter(bird_batch=batch, date=target_date).order_by("-pk").first()
    )
    room_record_map = _resolve_room_record_map(room_ids, production_record)

    session_snapshots = _build_session_snapshots(
        rooms=rooms,
        locations=locations,
        target_date=target_date,
        tolerance_percent=uniformity_tolerance_percent,
    )

    recent_sessions = _build_recent_session_summaries(
        rooms=rooms,
        target_date=target_date,
        tolerance_percent=uniformity_tolerance_percent,
    )

    context_token = _generate_batch_context_token(
        actor=actor,
        batch=batch,
        target_date=target_date,
    )

    return WeightRegistry(
        date=target_date,
        task_assignment_id=None,
        task_definition_id=None,
        production_record_id=production_record.pk if production_record else None,
        room_record_ids=room_record_map,
        context_token=context_token,
        unit_label="g",
        min_sample_size=min_sample_size,
        uniformity_tolerance_percent=uniformity_tolerance_percent,
        locations=tuple(locations),
        sessions=tuple(session_snapshots),
        recent_sessions=tuple(recent_sessions),
        resume_hint="",
    )


def _collect_rooms_from_allocations(
    allocations: Iterable[BirdBatchRoomAllocation],
) -> list[Room]:
    rooms: "OrderedDict[int, Room]" = OrderedDict()
    for allocation in allocations:
        if allocation.room_id and allocation.room_id not in rooms:
            rooms[allocation.room_id] = allocation.room
    return list(rooms.values())


def _aggregate_birds_per_room(allocations: Iterable[BirdBatchRoomAllocation]) -> Mapping[int, int]:
    birds: dict[int, int] = {}
    for allocation in allocations:
        if not allocation.room_id:
            continue
        birds.setdefault(allocation.room_id, 0)
        birds[allocation.room_id] += int(allocation.quantity or 0)
    return birds


def _build_locations(rooms: Iterable[Room], birds_map: Mapping[int, int]) -> list[WeightLocation]:
    locations: list[WeightLocation] = []
    for room in rooms:
        chicken_house = room.chicken_house
        farm = chicken_house.farm if chicken_house else None
        label_parts = []
        if farm:
            label_parts.append(farm.name)
        if chicken_house:
            label_parts.append(chicken_house.name)
        label_parts.append(room.name)
        locations.append(
            WeightLocation(
                identifier=f"room-{room.pk}",
                room_id=room.pk,
                label=" · ".join(label_parts),
                farm_name=farm.name if farm else None,
                barn_name=chicken_house.name if chicken_house else None,
                room_name=room.name,
                birds=birds_map.get(room.pk),
            )
        )
    return locations


def _resolve_room_record_map(
    room_ids: list[int],
    production_record: Optional[ProductionRecord],
) -> dict[int, int]:
    if not production_record or not room_ids:
        return {}
    room_records = (
        ProductionRoomRecord.objects.filter(production_record=production_record, room_id__in=room_ids)
        .values("room_id", "id")
        .order_by("room_id")
    )
    return {row["room_id"]: row["id"] for row in room_records}


def _build_session_snapshots(
    *,
    rooms: Iterable[Room],
    locations: Iterable[WeightLocation],
    target_date: date,
    tolerance_percent: int,
) -> list[WeightSessionSnapshot]:
    location_by_room = {location.room_id: location for location in locations}
    sessions = (
        WeightSampleSession.objects.select_related(
            "room",
            "room__chicken_house",
            "room__chicken_house__farm",
            "created_by",
            "updated_by",
        )
        .prefetch_related("samples")
        .filter(date=target_date, room__in=list(rooms))
        .order_by("room__name")
    )
    snapshots: list[WeightSessionSnapshot] = []
    for session in sessions:
        location = location_by_room.get(session.room_id)
        if not location:
            continue
        entries = tuple(sample.grams for sample in session.samples.all())
        metrics = _compute_metrics(entries, session.tolerance_percent or tolerance_percent)
        snapshots.append(
            WeightSessionSnapshot(
                session_id=session.pk,
                location_id=location.identifier,
                room_id=session.room_id,
                birds=session.birds,
                entries=entries,
                metrics=metrics,
                created_at=session.created_at,
                updated_at=session.updated_at,
                submitted_at=session.submitted_at,
                created_by_display=_serialize_user_display(session.created_by),
                updated_by_display=_serialize_user_display(session.updated_by),
            )
        )
    return snapshots


def _build_recent_session_summaries(
    *,
    rooms: Iterable[Room],
    target_date: date,
    tolerance_percent: int,
) -> list[WeightSessionSummary]:
    recent_sessions_queryset = (
        WeightSampleSession.objects.select_related("room", "room__chicken_house")
        .prefetch_related("samples")
        .filter(room__in=list(rooms))
        .exclude(date__lt=target_date - timedelta(days=30))
        .order_by("-submitted_at", "-updated_at")[:5]
    )
    summaries: list[WeightSessionSummary] = []
    for session in recent_sessions_queryset:
        metrics = _compute_metrics(tuple(sample.grams for sample in session.samples.all()), session.tolerance_percent or tolerance_percent)
        summaries.append(
            WeightSessionSummary(
                session_id=session.pk,
                barn_label=getattr(session.room.chicken_house, "name", None) if session.room else None,
                room_label=getattr(session.room, "name", None),
                date=session.date,
                average_grams=metrics.average_grams,
                uniformity_percent=metrics.uniformity_percent,
                sample_size=metrics.count,
            )
        )
    return summaries


def _compute_metrics(entries: Iterable[Decimal], tolerance_percent: int) -> WeightMetrics:
    entries = tuple(entries)
    total = len(entries)
    if not total:
        return WeightMetrics(
            count=0,
            average_grams=None,
            variance_grams=None,
            min_grams=None,
            max_grams=None,
            uniformity_percent=None,
            within_tolerance=0,
            tolerance_percent=tolerance_percent,
        )

    sum_values = sum(entries, start=Decimal("0"))
    average = sum_values / Decimal(total)
    min_value = min(entries)
    max_value = max(entries)
    variance_accumulator = Decimal("0")
    tolerance_value = average * Decimal(tolerance_percent) / Decimal(100)
    lower_bound = average - tolerance_value
    upper_bound = average + tolerance_value
    within_tolerance = 0
    for entry in entries:
        variance_accumulator += (entry - average) ** 2
        if lower_bound <= entry <= upper_bound:
            within_tolerance += 1
    variance = variance_accumulator / Decimal(total)
    uniformity = Decimal(within_tolerance) / Decimal(total) * Decimal(100)

    return WeightMetrics(
        count=total,
        average_grams=average.quantize(DECIMAL_QUANTIZE),
        variance_grams=variance.quantize(DECIMAL_QUANTIZE),
        min_grams=min_value.quantize(DECIMAL_QUANTIZE),
        max_grams=max_value.quantize(DECIMAL_QUANTIZE),
        uniformity_percent=uniformity.quantize(DECIMAL_QUANTIZE),
        within_tolerance=within_tolerance,
        tolerance_percent=tolerance_percent,
    )


def _serialize_user_display(user) -> Optional[str]:
    if not user:
        return None
    if hasattr(user, "get_full_name"):
        value = user.get_full_name() or user.get_username()
    else:
        value = getattr(user, "username", None)
    return value.strip() if value else None


def _generate_batch_context_token(*, actor: Optional[User], batch: BirdBatch, target_date: date) -> str:
    user_part = str(actor.pk) if actor and actor.pk else "anonymous"
    raw = "|".join((user_part, "batch", str(batch.pk), target_date.isoformat()))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

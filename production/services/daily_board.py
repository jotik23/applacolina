from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Mapping, Optional, TYPE_CHECKING

from django.core.exceptions import ValidationError
from django.db import transaction

from production.models import BirdBatch, BirdBatchRoomAllocation, ProductionRecord, ProductionRoomRecord

if TYPE_CHECKING:
    from personal.models import UserProfile


@dataclass(frozen=True)
class RoomEntry:
    production: int
    consumption: int
    mortality: int
    discard: int


@dataclass(frozen=True)
class RoomEntryTotals:
    production: Decimal
    consumption: Decimal
    mortality: int
    discard: int


def save_daily_room_entries(
    *,
    batch: BirdBatch,
    date: date,
    entries: Mapping[int, RoomEntry],
    average_egg_weight: Optional[Decimal],
    actor: Optional["UserProfile"],
) -> ProductionRecord:
    """Persist aggregated production data plus its room-level distribution."""
    if not entries:
        raise ValidationError("Debes registrar al menos un salón antes de guardar los datos.")

    with transaction.atomic():
        allocations = list(
            BirdBatchRoomAllocation.objects.select_for_update()
            .filter(bird_batch=batch)
            .values_list("room_id", flat=True)
        )
        required_room_ids = set(allocations)
        if not required_room_ids:
            raise ValidationError("El lote no tiene salones asignados. Configura la distribución antes de registrar.")

        provided_room_ids = set(entries.keys())
        missing_rooms = required_room_ids - provided_room_ids
        if missing_rooms:
            raise ValidationError("Debes completar los datos de todos los salones asignados al lote.")

        extraneous_rooms = provided_room_ids - required_room_ids
        if extraneous_rooms:
            raise ValidationError("Intentaste registrar salones que no pertenecen al lote.")

        totals = _compute_totals(entries)

        record, created = ProductionRecord.objects.select_for_update().get_or_create(
            bird_batch=batch,
            date=date,
            defaults={
                "production": totals.production,
                "consumption": totals.consumption,
                "mortality": totals.mortality,
                "discard": totals.discard,
                "average_egg_weight": average_egg_weight,
                "created_by": actor,
                "updated_by": actor,
            },
        )

        if not created:
            record.production = totals.production
            record.consumption = totals.consumption
            record.mortality = totals.mortality
            record.discard = totals.discard
            record.average_egg_weight = average_egg_weight
            if record.created_by_id is None and actor:
                record.created_by = actor
            record.updated_by = actor

        record.full_clean()
        record.save()

        existing_room_records = {
            room_record.room_id: room_record
            for room_record in ProductionRoomRecord.objects.select_for_update().filter(production_record=record)
        }

        for room_id, entry in entries.items():
            room_record = existing_room_records.get(room_id)
            if room_record is None:
                room_record = ProductionRoomRecord(production_record=record, room_id=room_id)

            room_record.production = Decimal(entry.production)
            room_record.consumption = Decimal(entry.consumption)
            room_record.mortality = entry.mortality
            room_record.discard = entry.discard
            room_record.full_clean()
            room_record.save()

        ProductionRoomRecord.objects.filter(production_record=record).exclude(room_id__in=entries).delete()
        return record


def _compute_totals(entries: Mapping[int, RoomEntry]) -> RoomEntryTotals:
    production_total = Decimal("0")
    consumption_total = Decimal("0")
    mortality_total = 0
    discard_total = 0

    for entry in entries.values():
        production_total += Decimal(entry.production)
        consumption_total += Decimal(entry.consumption)
        mortality_total += entry.mortality
        discard_total += entry.discard

    return RoomEntryTotals(
        production=production_total,
        consumption=consumption_total,
        mortality=mortality_total,
        discard=discard_total,
    )

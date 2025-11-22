from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable, Mapping, Optional

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _

from personal.models import ShiftType, UserProfile
from production.models import BirdBatch, BirdBatchRoomAllocation, ProductionRecord, ProductionRoomRecord

from .production_registry import resolve_assignment_for_date, _coerce_int


@dataclass(frozen=True)
class NightMortalityRoomSnapshot:
    room_id: int
    label: str
    chicken_house: str
    allocated_birds: int
    mortality: Optional[int]
    discard: Optional[int]


@dataclass(frozen=True)
class NightMortalityLotSnapshot:
    batch_id: int
    label: str
    farm_name: str
    chicken_house_names: tuple[str, ...]
    rooms: tuple[NightMortalityRoomSnapshot, ...]
    allocated_birds: int

    @property
    def room_ids(self) -> set[int]:
        return {room.room_id for room in self.rooms}

    @property
    def has_entries(self) -> bool:
        return any(
            (room.mortality not in (None, "")) or (room.discard not in (None, ""))
            for room in self.rooms
        )


@dataclass(frozen=True)
class NightMortalityRegistry:
    date: date
    assignment_id: int
    farm_name: str
    shift_label: Optional[str]
    lots: tuple[NightMortalityLotSnapshot, ...]

    @property
    def date_label(self) -> str:
        return date_format(self.date, "DATE_FORMAT")

    @property
    def weekday_label(self) -> str:
        return date_format(self.date, "l").capitalize()

    @property
    def total_birds(self) -> int:
        return sum(lot.allocated_birds for lot in self.lots)

    @property
    def has_records(self) -> bool:
        return any(lot.has_entries for lot in self.lots)


def build_night_mortality_registry(
    *,
    user: Optional[UserProfile],
    reference_date: Optional[date] = None,
) -> Optional[NightMortalityRegistry]:
    if not user or not getattr(user, "is_authenticated", False):
        return None
    if not user.has_perm("task_manager.view_mini_app_task_cards"):
        return None

    target_date = reference_date or UserProfile.colombia_today()
    assignment = resolve_assignment_for_date(user=user, target_date=target_date)
    if not assignment or not assignment.position:
        return None

    position = assignment.position
    category = getattr(position, "category", None)
    if not category or category.shift_type != ShiftType.NIGHT:
        return None

    farm = position.farm or getattr(position.chicken_house, "farm", None)
    if not farm:
        return None

    allocation_queryset = (
        BirdBatchRoomAllocation.objects.select_related("room", "room__chicken_house")
        .filter(room__chicken_house__farm=farm)
        .order_by("room__chicken_house__name", "room__name")
    )
    batches_queryset = (
        BirdBatch.objects.filter(status=BirdBatch.Status.ACTIVE, farm=farm)
        .select_related("farm")
        .prefetch_related(
            models.Prefetch(
                "allocations",
                queryset=allocation_queryset,
                to_attr="farm_allocations",
            )
        )
        .order_by("pk")
    )

    batches: list[BirdBatch] = list(batches_queryset)
    if not batches:
        return None

    room_record_queryset = ProductionRoomRecord.objects.select_related("room", "room__chicken_house")
    records = {
        record.bird_batch_id: record
        for record in ProductionRecord.objects.select_related("created_by", "updated_by")
        .prefetch_related(models.Prefetch("room_records", queryset=room_record_queryset))
        .filter(bird_batch__in=batches, date=target_date)
    }

    lots: list[NightMortalityLotSnapshot] = []
    for batch in batches:
        allocations = getattr(batch, "farm_allocations", None) or []
        if not allocations:
            continue

        allocated_birds = sum(allocation.quantity or 0 for allocation in allocations)
        record = records.get(batch.pk)
        room_records_map = {
            room_record.room_id: room_record
            for room_record in record.room_records.all()
        } if record else {}

        room_snapshots: list[NightMortalityRoomSnapshot] = []
        house_labels: list[str] = []
        for allocation in allocations:
            room = allocation.room
            chicken_house = room.chicken_house
            house_name = chicken_house.name if chicken_house else ""
            if house_name:
                house_labels.append(house_name)

            room_record = room_records_map.get(allocation.room_id)
            room_snapshots.append(
                NightMortalityRoomSnapshot(
                    room_id=allocation.room_id,
                    label=room.name,
                    chicken_house=house_name,
                    allocated_birds=allocation.quantity or 0,
                    mortality=room_record.mortality if room_record else None,
                    discard=room_record.discard if room_record else None,
                )
            )

        if not room_snapshots:
            continue

        ordered_houses = tuple(dict.fromkeys(house_labels))
        lots.append(
            NightMortalityLotSnapshot(
                batch_id=batch.pk,
                label=str(batch),
                farm_name=batch.farm.name if batch.farm_id else "",
                chicken_house_names=ordered_houses,
                rooms=tuple(room_snapshots),
                allocated_birds=allocated_birds,
            )
        )

    if not lots:
        return None

    shift_label = category.get_shift_type_display() if hasattr(category, "get_shift_type_display") else None
    return NightMortalityRegistry(
        date=target_date,
        assignment_id=assignment.pk,
        farm_name=farm.name,
        shift_label=shift_label,
        lots=tuple(lots),
    )


def serialize_night_mortality_registry(registry: NightMortalityRegistry) -> dict[str, object]:
    payload = {
        "date": registry.date.isoformat(),
        "date_label": registry.date_label,
        "weekday_label": registry.weekday_label,
        "farm": registry.farm_name,
        "shift_label": registry.shift_label,
        "total_birds": registry.total_birds,
        "lots": [],
        "has_records": registry.has_records,
    }

    for lot in registry.lots:
        lot_payload = {
            "id": lot.batch_id,
            "label": lot.label,
            "farm": lot.farm_name,
            "houses": list(lot.chicken_house_names),
            "birds": lot.allocated_birds,
            "rooms": [],
        }
        for room in lot.rooms:
            lot_payload["rooms"].append(
                {
                    "id": room.room_id,
                    "label": room.label,
                    "house": room.chicken_house,
                    "birds": room.allocated_birds,
                    "mortality": room.mortality,
                    "discard": room.discard,
                }
            )
        payload["lots"].append(lot_payload)

    return payload


def persist_night_mortality_entries(
    *,
    registry: NightMortalityRegistry,
    entries: Iterable[Mapping[str, object]],
    user: UserProfile,
) -> list[ProductionRecord]:
    if not entries:
        raise ValidationError(_("Debes enviar al menos un lote a registrar."))

    lot_by_id = {lot.batch_id: lot for lot in registry.lots}
    if not lot_by_id:
        raise ValidationError(_("No se encontraron lotes activos en tu granja."))

    saved_records: list[ProductionRecord] = []
    with transaction.atomic():
        for entry in entries:
            if not isinstance(entry, Mapping):
                raise ValidationError(_("Formato de lote inválido."))

            batch_id = entry.get("bird_batch") or entry.get("id")
            try:
                batch_id = int(str(batch_id))
            except (TypeError, ValueError):
                raise ValidationError(_("El lote enviado es inválido."))

            lot = lot_by_id.get(batch_id)
            if not lot:
                raise ValidationError(_("El lote %(batch)s no pertenece a tu granja."), params={"batch": batch_id})

            rooms_payload = entry.get("rooms")
            if not isinstance(rooms_payload, list) or not rooms_payload:
                raise ValidationError(
                    _("Debes enviar los salones para el lote %(batch)s."),
                    params={"batch": batch_id},
                )

            parsed_rooms: dict[int, dict[str, int]] = {}
            for room_payload in rooms_payload:
                if not isinstance(room_payload, Mapping):
                    raise ValidationError(_("Formato de salón inválido."))

                raw_room_id = room_payload.get("room_id") or room_payload.get("id")
                try:
                    room_id = int(str(raw_room_id))
                except (TypeError, ValueError):
                    raise ValidationError(_("El identificador del salón es inválido."))

                if room_id not in lot.room_ids:
                    raise ValidationError(
                        _("El salón %(room)s no pertenece al lote %(batch)s."),
                        params={"room": room_id, "batch": batch_id},
                    )

                mortality = _coerce_int(room_payload.get("mortality"), field="mortality", allow_empty=True)
                discard = _coerce_int(room_payload.get("discard"), field="discard", allow_empty=True)
                parsed_rooms[room_id] = {
                    "mortality": mortality,
                    "discard": discard,
                }

            missing_rooms = lot.room_ids - set(parsed_rooms.keys())
            if missing_rooms:
                raise ValidationError(
                    _("Debes enviar todos los salones asignados para el lote %(batch)s."),
                    params={"batch": batch_id},
                )

            total_mortality = sum(values["mortality"] for values in parsed_rooms.values())
            total_discard = sum(values["discard"] for values in parsed_rooms.values())
            record = _persist_mortality_for_lot(
                lot=lot,
                registry=registry,
                room_values=parsed_rooms,
                total_mortality=total_mortality,
                total_discard=total_discard,
                user=user,
            )
            saved_records.append(record)
    return saved_records


def _persist_mortality_for_lot(
    *,
    lot: NightMortalityLotSnapshot,
    registry: NightMortalityRegistry,
    room_values: Mapping[int, Mapping[str, int]],
    total_mortality: int,
    total_discard: int,
    user: UserProfile,
) -> ProductionRecord:
    record_defaults = {
        "production": Decimal("0"),
        "consumption": Decimal("0"),
        "mortality": total_mortality,
        "discard": total_discard,
        "created_by": user,
        "updated_by": user,
    }
    record, created = ProductionRecord.objects.select_for_update().get_or_create(
        bird_batch_id=lot.batch_id,
        date=registry.date,
        defaults=record_defaults,
    )

    if not created:
        record.mortality = total_mortality
        record.discard = total_discard
        if record.created_by_id is None:
            record.created_by = user
        record.updated_by = user
    record.full_clean()
    record.save()

    existing_room_records = {
        room_record.room_id: room_record
        for room_record in ProductionRoomRecord.objects.select_for_update()
        .filter(production_record=record)
    }
    for room_id, values in room_values.items():
        room_record = existing_room_records.get(room_id)
        if room_record is None:
            room_record = ProductionRoomRecord(
                production_record=record,
                room_id=room_id,
                production=Decimal("0"),
                consumption=Decimal("0"),
                mortality=0,
                discard=0,
            )
        room_record.mortality = values["mortality"]
        room_record.discard = values["discard"]
        if room_record.production is None:
            room_record.production = Decimal("0")
        if room_record.consumption is None:
            room_record.consumption = Decimal("0")
        if room_record.discard is None:
            room_record.discard = 0
        room_record.full_clean()
        room_record.save()

    return record

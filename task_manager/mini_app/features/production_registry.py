from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Iterable, Mapping, Optional

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Case, IntegerField, Value, When
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _

from personal.models import CalendarStatus, ShiftAssignment, UserProfile
from production.models import BirdBatch, BirdBatchRoomAllocation, ProductionRecord, ProductionRoomRecord


ACTIVE_CALENDAR_STATES = (
    CalendarStatus.MODIFIED,
    CalendarStatus.APPROVED,
    CalendarStatus.DRAFT,
)

_STATUS_PRIORITY = Case(
    When(calendar__status=CalendarStatus.MODIFIED, then=Value(0)),
    When(calendar__status=CalendarStatus.APPROVED, then=Value(1)),
    When(calendar__status=CalendarStatus.DRAFT, then=Value(2)),
    default=Value(3),
    output_field=IntegerField(),
)

PRODUCTION_STEP = Decimal("0.01")


@dataclass(frozen=True)
class ProductionRecordSnapshot:
    production_total: Decimal
    consumption_total: Decimal
    mortality_total: int
    discard_total: int
    average_egg_weight: Optional[Decimal]
    recorded_at: datetime
    updated_at: datetime
    created_by_display: Optional[str]
    updated_by_display: Optional[str]

    @property
    def last_actor_display(self) -> Optional[str]:
        return self.updated_by_display or self.created_by_display

    @property
    def last_updated_at(self) -> datetime:
        return self.updated_at or self.recorded_at


@dataclass(frozen=True)
class ProductionRoomSnapshot:
    room_id: int
    label: str
    allocated_birds: int
    production: Optional[Decimal]
    consumption: Optional[Decimal]
    mortality: Optional[int]
    discard: Optional[int]


@dataclass(frozen=True)
class ProductionLot:
    batch_id: int
    label: str
    farm_name: str
    chicken_house_name: Optional[str]
    rooms: tuple[ProductionRoomSnapshot, ...]
    allocated_birds: int
    record: Optional[ProductionRecordSnapshot]

    @property
    def room_labels(self) -> tuple[str, ...]:
        return tuple(room.label for room in self.rooms)

    @property
    def room_ids(self) -> set[int]:
        return {room.room_id for room in self.rooms}


@dataclass(frozen=True)
class ProductionRegistry:
    date: date
    assignment_id: int
    position_label: str
    chicken_house_name: Optional[str]
    farm_name: Optional[str]
    lots: tuple[ProductionLot, ...]

    @property
    def active_hens(self) -> int:
        return sum(lot.allocated_birds for lot in self.lots)

    @property
    def date_label(self) -> str:
        return date_format(self.date, "DATE_FORMAT")

    @property
    def weekday_label(self) -> str:
        return date_format(self.date, "l").capitalize()


def resolve_assignment_for_date(*, user: UserProfile, target_date: date) -> Optional[ShiftAssignment]:
    if not user.is_active:
        return None

    return (
        ShiftAssignment.objects.select_related(
            "calendar",
            "position",
            "position__category",
            "position__farm",
            "position__chicken_house",
        )
        .prefetch_related("position__rooms")
        .filter(
            operator_id=user.pk,
            date=target_date,
            calendar__status__in=ACTIVE_CALENDAR_STATES,
            calendar__start_date__lte=target_date,
            calendar__end_date__gte=target_date,
        )
        .order_by(
            _STATUS_PRIORITY,
            "-calendar__updated_at",
            "-calendar__created_at",
            "calendar_id",
        )
        .first()
    )


def build_production_registry(
    *,
    user: Optional[UserProfile],
    reference_date: Optional[date] = None,
) -> Optional[ProductionRegistry]:
    if not user or not getattr(user, "is_authenticated", False):
        return None
    if not user.has_perm("task_manager.view_mini_app_production_card"):
        return None

    target_date = reference_date or UserProfile.colombia_today()
    assignment = resolve_assignment_for_date(user=user, target_date=target_date)
    if not assignment or not assignment.position:
        return None

    position = assignment.position
    chicken_house = position.chicken_house
    if not chicken_house:
        return None

    room_ids = list(position.rooms.values_list("pk", flat=True))

    allocation_queryset = (
        BirdBatchRoomAllocation.objects.select_related("room", "room__chicken_house")
        .filter(room__chicken_house=chicken_house)
        .order_by("room__name")
    )
    if room_ids:
        allocation_queryset = allocation_queryset.filter(room_id__in=room_ids)

    batches_queryset = (
        BirdBatch.objects.filter(
            status=BirdBatch.Status.ACTIVE,
            allocations__room__chicken_house=chicken_house,
        )
        .select_related("farm")
        .prefetch_related(
            models.Prefetch(
                "allocations",
                queryset=allocation_queryset,
                to_attr="filtered_allocations",
            )
        )
        .order_by("pk")
        .distinct()
    )
    if room_ids:
        batches_queryset = batches_queryset.filter(allocations__room_id__in=room_ids)

    batches: list[BirdBatch] = list(batches_queryset)
    if not batches:
        return None

    room_record_queryset = ProductionRoomRecord.objects.select_related("room", "room__chicken_house")
    records = {
        record.bird_batch_id: record
        for record in ProductionRecord.objects.select_related("created_by", "updated_by")
        .prefetch_related(models.Prefetch("room_records", queryset=room_record_queryset))
        .filter(bird_batch__in=batches, date=target_date)
        .all()
    }

    lots: list[ProductionLot] = []
    for batch in batches:
        allocations = getattr(batch, "filtered_allocations", None) or []
        if not allocations:
            continue

        allocated_birds = sum(allocation.quantity or 0 for allocation in allocations)

        record = records.get(batch.pk)
        record_snapshot: Optional[ProductionRecordSnapshot] = None
        room_records_map: dict[int, ProductionRoomRecord] = {}
        if record:
            created_by_display = _display_user(record.created_by)
            updated_by_display = _display_user(record.updated_by)
            record_snapshot = ProductionRecordSnapshot(
                production_total=_quantize_production(record.production) or Decimal("0"),
                consumption_total=_quantize_to_int(record.consumption) or Decimal(0),
                mortality_total=record.mortality,
                discard_total=record.discard,
                average_egg_weight=record.average_egg_weight,
                recorded_at=record.recorded_at,
                updated_at=record.updated_at,
                created_by_display=created_by_display,
                updated_by_display=updated_by_display,
            )
            room_records_map = {room_record.room_id: room_record for room_record in record.room_records.all()}

        room_snapshots: list[ProductionRoomSnapshot] = []
        for allocation in allocations:
            room = allocation.room
            room_record = room_records_map.get(allocation.room_id)
            room_snapshots.append(
                ProductionRoomSnapshot(
                    room_id=allocation.room_id,
                    label=room.name,
                    allocated_birds=allocation.quantity or 0,
                    production=_quantize_production(room_record.production) if room_record else None,
                    consumption=_quantize_to_int(room_record.consumption) if room_record else None,
                    mortality=room_record.mortality if room_record else None,
                    discard=room_record.discard if room_record else None,
                )
            )

        lots.append(
            ProductionLot(
                batch_id=batch.pk,
                label=str(batch),
                farm_name=batch.farm.name if batch.farm_id else "",
                chicken_house_name=chicken_house.name if chicken_house else None,
                rooms=tuple(room_snapshots),
                allocated_birds=allocated_birds,
                record=record_snapshot,
            )
        )

    if not lots:
        return None

    return ProductionRegistry(
        date=target_date,
        assignment_id=assignment.pk,
        position_label=position.name,
        chicken_house_name=chicken_house.name if chicken_house else None,
        farm_name=position.farm.name if position.farm_id else None,
        lots=tuple(lots),
    )


def serialize_production_registry(registry: ProductionRegistry) -> dict[str, object]:
    has_records = any(lot.record is not None for lot in registry.lots)
    payload = {
        "date": registry.date.isoformat(),
        "date_label": registry.date_label,
        "weekday_label": registry.weekday_label,
        "position_label": registry.position_label,
        "chicken_house": registry.chicken_house_name,
        "farm": registry.farm_name,
        "active_hens": registry.active_hens,
        "lots": [],
        "has_records": has_records,
    }

    for lot in registry.lots:
        lot_payload = {
            "id": lot.batch_id,
            "label": lot.label,
            "farm": lot.farm_name,
            "chicken_house": lot.chicken_house_name,
            "room_labels": list(lot.room_labels),
            "rooms": [],
            "birds": lot.allocated_birds,
            "record": None,
        }
        for room in lot.rooms:
            room_payload = {
                "id": room.room_id,
                "label": room.label,
                "birds": room.allocated_birds,
                "production": _format_decimal(room.production) if room.production is not None else None,
                "consumption": _format_decimal(room.consumption) if room.consumption is not None else None,
                "mortality": room.mortality,
                "discard": room.discard,
            }
            lot_payload["rooms"].append(room_payload)
        if lot.record:
            record = lot.record
            record_payload = {
                "production": _format_decimal(record.production_total),
                "consumption": _format_decimal(record.consumption_total),
                "mortality": record.mortality_total,
                "discard": record.discard_total,
                "average_egg_weight": (
                    _format_decimal(record.average_egg_weight) if record.average_egg_weight is not None else None
                ),
                "last_actor": record.last_actor_display,
                "last_updated_at": timezone.localtime(record.last_updated_at).isoformat(),
                "last_updated_label": date_format(timezone.localtime(record.last_updated_at), "DATETIME_FORMAT"),
            }
            lot_payload["record"] = record_payload
        payload["lots"].append(lot_payload)

    return payload


def persist_production_records(
    *,
    registry: ProductionRegistry,
    entries: Iterable[dict[str, object]],
    user: UserProfile,
) -> list[ProductionRecord]:
    if not entries:
        raise ValidationError(_("Debes enviar al menos un lote a registrar."))

    lot_by_id = {lot.batch_id: lot for lot in registry.lots}
    if not lot_by_id:
        raise ValidationError(_("No se encontraron lotes activos para registrar."))

    saved_records: list[ProductionRecord] = []
    with transaction.atomic():
        for entry in entries:
            batch_id = entry.get("bird_batch")
            if batch_id not in lot_by_id:
                raise ValidationError(_("El lote %(batch)s no es válido para tu posición."), params={"batch": batch_id})

            record = _persist_single_record(
                lot=lot_by_id[batch_id],
                registry=registry,
                entry=entry,
                user=user,
            )
            saved_records.append(record)
    return saved_records


def _persist_single_record(
    *,
    lot: ProductionLot,
    registry: ProductionRegistry,
    entry: dict[str, object],
    user: UserProfile,
) -> ProductionRecord:
    if entry is None:
        raise ValidationError(_("Formato de lote inválido."))

    rooms_payload = entry.get("rooms")
    if not isinstance(rooms_payload, list) or not rooms_payload:
        raise ValidationError(
            _("Debes enviar los salones para el lote %(batch)s."),
            params={"batch": lot.batch_id},
        )

    parsed_rooms: dict[int, dict[str, object]] = {}
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
                params={"room": room_id, "batch": lot.batch_id},
            )

        production = _coerce_decimal(
            room_payload.get("production"),
            field="production",
        )
        production = _quantize_production(production)
        consumption = _coerce_decimal(
            room_payload.get("consumption"),
            field="consumption",
            allow_decimals=False,
        )
        mortality = _coerce_int(room_payload.get("mortality"), field="mortality", allow_empty=True)
        discard = _coerce_int(room_payload.get("discard"), field="discard", allow_empty=True)
        parsed_rooms[room_id] = {
            "production": production,
            "consumption": consumption,
            "mortality": mortality,
            "discard": discard,
        }

    missing_rooms = lot.room_ids - set(parsed_rooms.keys())
    if missing_rooms:
        raise ValidationError(
            _("Debes enviar todos los salones asignados para el lote %(batch)s."),
            params={"batch": lot.batch_id},
        )

    average_weight = _parse_average_weight(entry.get("average_egg_weight"))

    total_production = _quantize_production(sum(value["production"] for value in parsed_rooms.values()))
    total_consumption = _quantize_to_int(sum(value["consumption"] for value in parsed_rooms.values()))
    total_mortality = sum(value["mortality"] for value in parsed_rooms.values())
    total_discard = sum(value["discard"] for value in parsed_rooms.values())

    defaults = {
        "production": total_production,
        "consumption": total_consumption,
        "mortality": total_mortality,
        "discard": total_discard,
        "average_egg_weight": average_weight,
        "created_by": user,
        "updated_by": user,
    }

    record, created = ProductionRecord.objects.select_for_update().get_or_create(
        bird_batch_id=lot.batch_id,
        date=registry.date,
        defaults=defaults,
    )

    if not created:
        record.production = total_production
        record.consumption = total_consumption
        record.mortality = total_mortality
        record.discard = total_discard
        record.average_egg_weight = average_weight
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

    for room_id, values in parsed_rooms.items():
        room_record = existing_room_records.get(room_id)
        if room_record is None:
            room_record = ProductionRoomRecord(
                production_record=record,
                room_id=room_id,
            )
        room_record.production = values["production"]
        room_record.consumption = values["consumption"]
        room_record.mortality = values["mortality"]
        room_record.discard = values["discard"]
        room_record.full_clean()
        room_record.save()

    ProductionRoomRecord.objects.filter(production_record=record).exclude(room_id__in=parsed_rooms.keys()).delete()

    return record


def _parse_average_weight(raw_value: object) -> Optional[Decimal]:
    if raw_value in (None, "", "null"):
        return None
    return _coerce_decimal(
        raw_value,
        field="average_egg_weight",
        max_value=Decimal("9999999999.99"),
        allow_decimals=False,
    )


def _coerce_decimal(
    value: object,
    *,
    field: str,
    max_value: Optional[Decimal] = None,
    allow_decimals: bool = True,
) -> Decimal:
    if value in (None, "", "null"):
        raise ValidationError(_("El campo %(field)s es obligatorio."), params={"field": field})
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValidationError(_("El campo %(field)s debe ser un número."), params={"field": field})
    if decimal_value < 0:
        raise ValidationError(_("El campo %(field)s no puede ser negativo."), params={"field": field})
    if max_value is not None and abs(decimal_value) > max_value:
        raise ValidationError(
            _("El campo %(field)s excede el valor máximo permitido."),
            params={"field": field},
        )
    if not allow_decimals:
        if decimal_value != decimal_value.quantize(Decimal("1")):
            raise ValidationError(
                _("El campo %(field)s debe ser un número entero."),
                params={"field": field},
            )
        decimal_value = decimal_value.quantize(Decimal("1"))
    return decimal_value


def _coerce_int(value: object, *, field: str, allow_empty: bool = False) -> int:
    if value in (None, "", "null"):
        if allow_empty:
            return 0
        raise ValidationError(_("El campo %(field)s es obligatorio."), params={"field": field})
    try:
        integer_value = int(str(value))
    except (TypeError, ValueError):
        raise ValidationError(_("El campo %(field)s debe ser un número entero."), params={"field": field})
    if integer_value < 0:
        raise ValidationError(_("El campo %(field)s no puede ser negativo."), params={"field": field})
    return integer_value


def _format_decimal(value: Decimal) -> str:
    formatted = format(value, "f")
    if "." in formatted:
        formatted = formatted.rstrip("0").rstrip(".")
    return formatted


def _quantize_to_int(value: Optional[Decimal]) -> Optional[Decimal]:
    if value is None:
        return None
    return value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def _quantize_production(value: Optional[Decimal]) -> Optional[Decimal]:
    if value is None:
        return None
    quantized = value.quantize(PRODUCTION_STEP, rounding=ROUND_HALF_UP)
    if quantized < 0:
        return Decimal("0")
    return quantized


def _display_user(user: Optional[UserProfile]) -> Optional[str]:
    if not user:
        return None
    display_name = user.get_full_name() or user.get_username()
    return display_name.strip() or None

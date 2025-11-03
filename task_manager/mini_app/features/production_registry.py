from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Iterable, Optional

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Case, IntegerField, Value, When
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _

from personal.models import CalendarStatus, ShiftAssignment, UserProfile
from production.models import BirdBatch, BirdBatchRoomAllocation, ProductionRecord


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


@dataclass(frozen=True)
class ProductionRecordSnapshot:
    production: Decimal
    consumption: Decimal
    mortality: int
    discard: int
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
class ProductionLot:
    batch_id: int
    label: str
    farm_name: str
    chicken_house_name: Optional[str]
    rooms: tuple[str, ...]
    allocated_birds: int
    record: Optional[ProductionRecordSnapshot]


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
    if not user.is_active or not user.has_perm("task_manager.view_mini_app_production_card"):
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
        return ProductionRegistry(
            date=target_date,
            assignment_id=assignment.pk,
            position_label=position.name,
            chicken_house_name=chicken_house.name if chicken_house else None,
            farm_name=position.farm.name if position.farm_id else None,
            lots=tuple(),
        )

    records = {
        record.bird_batch_id: record
        for record in ProductionRecord.objects.select_related("created_by", "updated_by")
        .filter(bird_batch__in=batches, date=target_date)
        .all()
    }

    lots: list[ProductionLot] = []
    for batch in batches:
        allocations = getattr(batch, "filtered_allocations", None) or []
        if not allocations:
            continue

        rooms = tuple(sorted({allocation.room.name for allocation in allocations}))
        allocated_birds = sum(allocation.quantity or 0 for allocation in allocations)

        record = records.get(batch.pk)
        record_snapshot = None
        if record:
            created_by_display = _display_user(record.created_by)
            updated_by_display = _display_user(record.updated_by)
            record_snapshot = ProductionRecordSnapshot(
                production=record.production,
                consumption=record.consumption,
                mortality=record.mortality,
                discard=record.discard,
                average_egg_weight=record.average_egg_weight,
                recorded_at=record.recorded_at,
                updated_at=record.updated_at,
                created_by_display=created_by_display,
                updated_by_display=updated_by_display,
            )

        lots.append(
            ProductionLot(
                batch_id=batch.pk,
                label=str(batch),
                farm_name=batch.farm.name if batch.farm_id else "",
                chicken_house_name=chicken_house.name if chicken_house else None,
                rooms=rooms,
                allocated_birds=allocated_birds,
                record=record_snapshot,
            )
        )

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
            "rooms": list(lot.rooms),
            "birds": lot.allocated_birds,
            "record": None,
        }
        if lot.record:
            record = lot.record
            record_payload = {
                "production": _format_decimal(record.production),
                "consumption": _format_decimal(record.consumption),
                "mortality": record.mortality,
                "discard": record.discard,
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

    allowed_batch_ids = {lot.batch_id for lot in registry.lots}
    if not allowed_batch_ids:
        raise ValidationError(_("No se encontraron lotes activos para registrar."))

    saved_records: list[ProductionRecord] = []
    with transaction.atomic():
        for entry in entries:
            batch_id = entry.get("bird_batch")
            if batch_id not in allowed_batch_ids:
                raise ValidationError(_("El lote %(batch)s no es válido para tu posición."), params={"batch": batch_id})

            record = _persist_single_record(
                batch_id=batch_id,
                registry=registry,
                entry=entry,
                user=user,
            )
            saved_records.append(record)
    return saved_records


def _persist_single_record(*, batch_id: int, registry: ProductionRegistry, entry: dict[str, object], user: UserProfile) -> ProductionRecord:
    defaults = _parse_entry_payload(entry)
    defaults.update(
        {
            "created_by": user,
            "updated_by": user,
        }
    )

    record, created = ProductionRecord.objects.select_for_update().get_or_create(
        bird_batch_id=batch_id,
        date=registry.date,
        defaults=defaults,
    )

    if not created:
        for field, value in defaults.items():
            if field == "created_by":
                continue
            setattr(record, field, value)
        if record.created_by_id is None:
            record.created_by = user
        record.updated_by = user

    record.full_clean()
    record.save()
    return record


def _parse_entry_payload(entry: dict[str, object]) -> dict[str, object]:
    if entry is None:
        raise ValidationError(_("Formato de lote inválido."))

    try:
        production = _coerce_decimal(entry.get("production"), field="production")
        consumption = _coerce_decimal(entry.get("consumption"), field="consumption")
        mortality = _coerce_int(entry.get("mortality"), field="mortality")
        discard = _coerce_int(entry.get("discard"), field="discard")
    except ValidationError:
        raise

    avg_weight_raw = entry.get("average_egg_weight")
    average_egg_weight = None
    if avg_weight_raw not in (None, "", "null"):
        average_egg_weight = _coerce_decimal(
            avg_weight_raw,
            field="average_egg_weight",
            max_value=Decimal("9999999999.99"),
        )

    payload = {
        "production": production,
        "consumption": consumption,
        "mortality": mortality,
        "discard": discard,
        "average_egg_weight": average_egg_weight,
    }
    return payload


def _coerce_decimal(value: object, *, field: str, max_value: Optional[Decimal] = None) -> Decimal:
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
    return decimal_value


def _coerce_int(value: object, *, field: str) -> int:
    if value in (None, "", "null"):
        raise ValidationError(_("El campo %(field)s es obligatorio."), params={"field": field})
    try:
        integer_value = int(str(value))
    except (TypeError, ValueError):
        raise ValidationError(_("El campo %(field)s debe ser un número entero."), params={"field": field})
    if integer_value < 0:
        raise ValidationError(_("El campo %(field)s no puede ser negativo."), params={"field": field})
    return integer_value


def _format_decimal(value: Decimal) -> str:
    return format(value, "f")


def _display_user(user: Optional[UserProfile]) -> Optional[str]:
    if not user:
        return None
    display_name = user.get_full_name() or user.get_username()
    return display_name.strip() or None

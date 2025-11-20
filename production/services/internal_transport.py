from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable, Sequence

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Prefetch, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from personal.models import UserProfile
from production.models import EggClassificationBatch, Farm, ProductionRoomRecord


@dataclass(frozen=True)
class TransportBatchSnapshot:
    id: int
    production_date: date
    farm_name: str
    destination_name: str
    chicken_houses: list[str]
    rooms: list[str]
    cartons_confirmed: Decimal
    classified_cartons: Decimal
    pending_cartons: Decimal
    transport_status: str
    transport_status_label: str
    progress_step: str | None
    transporter_label: str | None
    expected_date: date | None
    verified_cartons: Decimal | None
    transporter_id: int | None
    confirmed_cartons: Decimal | None


def _room_prefetch() -> Prefetch:
    return Prefetch(
        "production_record__room_records",
        queryset=ProductionRoomRecord.objects.select_related(
            "room__chicken_house__farm",
            "room__chicken_house__egg_destination_farm",
        ).order_by("room__chicken_house__name", "room__name"),
        to_attr="transport_room_records",
    )


def collect_chicken_house_names(room_records: Iterable[ProductionRoomRecord]) -> list[str]:
    seen: dict[str, None] = {}
    for record in room_records:
        name = record.room.chicken_house.name
        if name not in seen:
            seen[name] = None
    return list(seen.keys())


def collect_room_names(room_records: Iterable[ProductionRoomRecord]) -> list[str]:
    seen: dict[str, None] = {}
    for record in room_records:
        name = record.room.name
        if name not in seen:
            seen[name] = None
    return list(seen.keys())


def _resolve_destination_name(batch: EggClassificationBatch, room_records: Iterable[ProductionRoomRecord]) -> str:
    if batch.transport_destination_farm:
        return batch.transport_destination_farm.name
    destinations: dict[str, None] = {}
    for record in room_records:
        destination = record.room.chicken_house.destination_farm
        if destination:
            destinations[destination.name] = None
    if destinations:
        return ", ".join(destinations.keys())
    return batch.bird_batch.farm.name


def _resolve_destination_farm(batch: EggClassificationBatch) -> Farm | None:
    if batch.transport_destination_farm:
        return batch.transport_destination_farm
    room_records = getattr(batch.production_record, "transport_room_records", None)
    if room_records is None:
        room_records = list(
            batch.production_record.room_records.select_related(
                "room__chicken_house__egg_destination_farm",
                "room__chicken_house__farm",
            )
        )
    for record in room_records:
        destination = record.room.chicken_house.destination_farm
        if destination:
            return destination
    return batch.bird_batch.farm


def _format_transporter_label(transporter: UserProfile | None) -> str | None:
    if not transporter:
        return None
    label = transporter.get_full_name() or transporter.cedula
    return label.strip() if label else None


def build_transport_snapshot(*, statuses: Iterable[str]) -> list[TransportBatchSnapshot]:
    qs = (
        EggClassificationBatch.objects.select_related(
            "bird_batch__farm",
            "transport_destination_farm",
            "transport_transporter",
            "production_record",
        )
        .prefetch_related(_room_prefetch())
        .annotate(classified_cartons=Coalesce(Sum("classification_entries__cartons"), Decimal("0")))
        .filter(transport_status__in=list(statuses))
        .order_by("production_record__date", "pk")
    )

    snapshot: list[TransportBatchSnapshot] = []
    for batch in qs:
        batch._classified_total_cache = Decimal(batch.classified_cartons)
        confirmed = Decimal(batch.received_cartons) if batch.received_cartons is not None else Decimal(batch.reported_cartons)
        pending = batch.pending_cartons
        if pending < 0:
            pending = Decimal("0")
        room_records = getattr(batch.production_record, "transport_room_records", [])
        chicken_houses = collect_chicken_house_names(room_records)
        rooms = collect_room_names(room_records)
        snapshot.append(
            TransportBatchSnapshot(
                id=batch.pk,
                production_date=batch.production_record.date,
                farm_name=batch.bird_batch.farm.name,
                destination_name=_resolve_destination_name(batch, room_records),
                chicken_houses=chicken_houses,
                rooms=rooms,
                cartons_confirmed=confirmed,
                classified_cartons=Decimal(batch.classified_cartons),
                pending_cartons=pending,
                transport_status=batch.transport_status,
                transport_status_label=batch.get_transport_status_display(),
                progress_step=batch.transport_progress_step or None,
                transporter_label=_format_transporter_label(batch.transport_transporter),
                expected_date=batch.transport_expected_date,
                verified_cartons=Decimal(batch.transport_verified_cartons)
                if batch.transport_verified_cartons is not None
                else None,
                transporter_id=batch.transport_transporter_id,
                confirmed_cartons=Decimal(batch.transport_confirmed_cartons)
                if batch.transport_confirmed_cartons is not None
                else None,
            )
        )
    return snapshot


def authorize_internal_transport(
    *,
    batch_ids: Sequence[int],
    transporter: UserProfile,
    expected_date: date,
    actor: UserProfile,
) -> list[EggClassificationBatch]:
    if not batch_ids:
        raise ValidationError(_("Selecciona al menos una producción para autorizar."))

    rooms_prefetch = _room_prefetch()
    batches = list(
        EggClassificationBatch.objects.select_related("bird_batch__farm", "transport_destination_farm", "production_record")
        .prefetch_related(rooms_prefetch)
        .filter(pk__in=batch_ids)
    )
    if not batches:
        raise ValidationError(_("No encontramos producciones para autorizar."))

    now = timezone.now()
    updated: list[EggClassificationBatch] = []
    with transaction.atomic():
        for batch in batches:
            if batch.transport_status == EggClassificationBatch.TransportStatus.VERIFIED:
                continue
            pending = batch.pending_cartons
            if pending <= Decimal("0"):
                continue
            destination = _resolve_destination_farm(batch)
            batch.transport_destination_farm = destination
            batch.transport_status = EggClassificationBatch.TransportStatus.AUTHORIZED
            batch.transport_transporter = transporter
            batch.transport_expected_date = expected_date
            batch.transport_authorized_at = now
            batch.transport_authorized_by = actor
            batch.transport_progress_step = ""
            batch.transport_verified_cartons = None
            batch.transport_verified_at = None
            batch.transport_verified_by = None
            batch.transport_confirmed_cartons = None
            batch.transport_confirmed_at = None
            batch.transport_confirmed_by = None
            batch.save(
                update_fields=[
                    "transport_destination_farm",
                    "transport_status",
                    "transport_transporter",
                    "transport_expected_date",
                    "transport_authorized_at",
                    "transport_authorized_by",
                    "transport_progress_step",
                    "transport_verified_cartons",
                    "transport_verified_at",
                    "transport_verified_by",
                    "transport_confirmed_cartons",
                    "transport_confirmed_at",
                    "transport_confirmed_by",
                    "updated_at",
                ]
            )
            updated.append(batch)
    if not updated:
        raise ValidationError(_("Las producciones seleccionadas ya fueron verificadas o no tienen cartones pendientes."))
    return updated


def update_transport_progress(*, step: str, actor: UserProfile) -> list[EggClassificationBatch]:
    valid_steps = {"verified", "loaded", "departed", "arrival", "unloading", "completed"}
    if step not in valid_steps:
        raise ValidationError(_("El estado de transporte enviado no es válido."))

    if step == "completed":
        target_status = EggClassificationBatch.TransportStatus.VERIFICATION
    elif step in {"departed", "arrival", "unloading"}:
        target_status = EggClassificationBatch.TransportStatus.IN_TRANSIT
    else:
        target_status = EggClassificationBatch.TransportStatus.AUTHORIZED

    eligible_statuses = [
        EggClassificationBatch.TransportStatus.AUTHORIZED,
        EggClassificationBatch.TransportStatus.IN_TRANSIT,
    ]
    if step == "completed":
        eligible_statuses.append(EggClassificationBatch.TransportStatus.VERIFICATION)

    batches = list(
        EggClassificationBatch.objects.filter(transport_status__in=eligible_statuses)
    )
    if not batches:
        raise ValidationError(_("No hay producciones en transporte para actualizar."))

    now = timezone.now()
    updated: list[EggClassificationBatch] = []
    with transaction.atomic():
        for batch in batches:
            batch.transport_progress_step = step
            batch.transport_status = target_status
            if step != "completed":
                batch.transport_verified_cartons = None
                batch.transport_verified_at = None
                batch.transport_verified_by = None
            batch.updated_at = now
            batch.save(
                update_fields=[
                    "transport_progress_step",
                    "transport_status",
                    "transport_verified_cartons",
                    "transport_verified_at",
                    "transport_verified_by",
                    "transport_confirmed_cartons",
                    "transport_confirmed_at",
                    "transport_confirmed_by",
                    "updated_at",
                ]
            )
            updated.append(batch)
    return updated


def record_transport_verification(
    *,
    entries: Sequence[dict[str, object]],
    actor: UserProfile,
) -> list[EggClassificationBatch]:
    if not entries:
        raise ValidationError(_("Debes enviar al menos una producción para verificar."))

    entry_map = {int(entry.get("id")): entry for entry in entries if entry.get("id") is not None}
    if not entry_map:
        raise ValidationError(_("No se encontraron producciones válidas para verificar."))

    batches = list(
        EggClassificationBatch.objects.filter(
            pk__in=entry_map.keys(),
            transport_status=EggClassificationBatch.TransportStatus.VERIFICATION,
        )
    )
    if not batches:
        raise ValidationError(_("No hay producciones pendientes de verificación."))

    now = timezone.now()
    updated: list[EggClassificationBatch] = []
    with transaction.atomic():
        for batch in batches:
            payload = entry_map.get(batch.pk)
            if payload is None:
                continue
            cartons_value = payload.get("cartons")
            try:
                cartons = Decimal(str(cartons_value))
            except (TypeError, ArithmeticError, ValueError):
                raise ValidationError(_("Los cartones enviados no son válidos."))
            if cartons < 0:
                raise ValidationError(_("Los cartones verificados no pueden ser negativos."))
            batch.transport_verified_cartons = cartons
            batch.transport_verified_at = now
            batch.transport_verified_by = actor
            batch.transport_status = EggClassificationBatch.TransportStatus.VERIFIED
            batch.save(
                update_fields=[
                    "transport_verified_cartons",
                    "transport_verified_at",
                    "transport_verified_by",
                    "transport_status",
                    "updated_at",
                ]
            )
            updated.append(batch)
    if not updated:
        raise ValidationError(_("No se actualizaron producciones durante la verificación."))
    return updated


def record_transporter_confirmation(
    *,
    entries: Sequence[dict[str, object]],
    actor: UserProfile,
) -> list[EggClassificationBatch]:
    if not entries:
        raise ValidationError(_("Debes enviar al menos una producción."))

    entry_map = {int(entry.get("id")): entry for entry in entries if entry.get("id") is not None}
    if not entry_map:
        raise ValidationError(_("No se encontraron producciones válidas."))

    batches = list(
        EggClassificationBatch.objects.filter(
            pk__in=entry_map.keys(),
            transport_status__in=[
                EggClassificationBatch.TransportStatus.AUTHORIZED,
                EggClassificationBatch.TransportStatus.IN_TRANSIT,
            ],
        )
    )
    if not batches:
        raise ValidationError(_("No hay producciones disponibles para confirmar."))

    now = timezone.now()
    updated: list[EggClassificationBatch] = []
    with transaction.atomic():
        for batch in batches:
            payload = entry_map.get(batch.pk)
            if payload is None:
                continue
            if batch.transport_transporter_id and batch.transport_transporter_id != actor.pk:
                raise ValidationError(
                    _("Solo el transportador asignado puede confirmar el lote %(lot)s." )
                    % {"lot": batch.bird_batch}
                )
            cartons_value = payload.get("cartons")
            try:
                cartons = Decimal(str(cartons_value))
            except (TypeError, ArithmeticError, ValueError):
                raise ValidationError(_("Los cartones enviados no son válidos."))
            if cartons < 0:
                raise ValidationError(_("Los cartones confirmados no pueden ser negativos."))
            batch.transport_confirmed_cartons = cartons
            batch.transport_confirmed_at = now
            batch.transport_confirmed_by = actor
            batch.save(
                update_fields=[
                    "transport_confirmed_cartons",
                    "transport_confirmed_at",
                    "transport_confirmed_by",
                    "updated_at",
                ]
            )
            updated.append(batch)
    if not updated:
        raise ValidationError(_("No se actualizaron producciones durante la confirmación."))
    return updated

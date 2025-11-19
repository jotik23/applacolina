from __future__ import annotations

from collections import OrderedDict
from datetime import timedelta
from decimal import Decimal
from typing import Iterable

from django.db.models import F, Prefetch, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _

from personal.models import UserProfile
from production.models import EggClassificationBatch, ProductionRecord, ProductionRoomRecord

TRANSPORT_WINDOW_DAYS = 14
_ROOM_DESTINATION_FILTER = Q(room__chicken_house__egg_destination_farm__isnull=False) & ~Q(
    room__chicken_house__egg_destination_farm=F("room__chicken_house__farm")
)
_RECORD_DESTINATION_FILTER = Q(
    room_records__room__chicken_house__egg_destination_farm__isnull=False
) & ~Q(room_records__room__chicken_house__egg_destination_farm=F("room_records__room__chicken_house__farm"))


def _build_transporters() -> list[dict[str, str]]:
    collaborators = (
        UserProfile.objects.filter(is_active=True)
        .order_by("nombres", "apellidos", "pk")
        .only("pk", "nombres", "apellidos", "telefono")
    )
    options: list[dict[str, str]] = []
    for collaborator in collaborators:
        label = (collaborator.get_full_name() or collaborator.cedula or str(collaborator.pk)).strip()
        contact = (collaborator.telefono or "").strip()
        options.append({"id": str(collaborator.pk), "label": label, "contact": contact})
    return options


def _collect_chicken_house_names(room_records: Iterable[ProductionRoomRecord]) -> list[str]:
    names: dict[str, None] = OrderedDict()
    for entry in room_records:
        house = entry.room.chicken_house
        if house.name not in names:
            names[house.name] = None
    return list(names.keys())


def _collect_room_names(room_records: Iterable[ProductionRoomRecord]) -> list[str]:
    names: dict[str, None] = OrderedDict()
    for entry in room_records:
        if entry.room.name not in names:
            names[entry.room.name] = None
    return list(names.keys())


def _load_classification_map(record_ids: list[int]) -> dict[int, dict[str, Decimal]]:
    queryset = (
        EggClassificationBatch.objects.filter(production_record_id__in=record_ids)
        .annotate(classified_cartons=Coalesce(Sum("classification_entries__cartons"), Decimal("0")))
        .values("production_record_id", "reported_cartons", "received_cartons", "classified_cartons")
    )
    data: dict[int, dict[str, Decimal]] = {}
    for row in queryset:
        source = Decimal(row["received_cartons"]) if row["received_cartons"] is not None else Decimal(row["reported_cartons"])
        data[row["production_record_id"]] = {
            "confirmed_cartons": source,
            "reported_cartons": Decimal(row["reported_cartons"]),
            "classified_cartons": Decimal(row["classified_cartons"]),
        }
    return data


def build_transport_queue_payload() -> dict[str, object]:
    today = timezone.localdate()
    start_date = today - timedelta(days=TRANSPORT_WINDOW_DAYS)
    room_prefetch = Prefetch(
        "room_records",
        queryset=ProductionRoomRecord.objects.select_related(
            "room__chicken_house__farm",
            "room__chicken_house__egg_destination_farm",
        )
        .filter(_ROOM_DESTINATION_FILTER)
        .order_by("room__chicken_house__name", "room__name"),
        to_attr="transfer_room_records",
    )
    records = list(
        ProductionRecord.objects.select_related("bird_batch")
        .prefetch_related(room_prefetch)
        .filter(date__gte=start_date)
        .filter(_RECORD_DESTINATION_FILTER)
        .order_by("date", "pk")
        .distinct()
    )

    record_ids = [record.pk for record in records]
    classification_map = _load_classification_map(record_ids) if record_ids else {}

    productions: list[dict[str, object]] = []
    total_cartons = Decimal("0")
    transporters = _build_transporters()
    for record in records:
        room_records = getattr(record, "transfer_room_records", None) or []
        if not room_records:
            continue
        batch = classification_map.get(record.pk)
        if not batch:
            continue
        confirmed_cartons = batch["confirmed_cartons"]
        classified_cartons = batch["classified_cartons"]
        if confirmed_cartons <= Decimal("0"):
            continue
        if classified_cartons >= confirmed_cartons:
            continue
        chicken_houses = _collect_chicken_house_names(room_records)
        if not chicken_houses:
            continue
        room_names = _collect_room_names(room_records)
        farm_names = OrderedDict()
        destination_names = OrderedDict()
        for entry in room_records:
            house = entry.room.chicken_house
            farm_names[house.farm.name] = None
            destination = house.destination_farm
            if destination:
                destination_names[destination.name] = None
        farms_label = ", ".join(farm_names.keys())
        label = _("Lote %(houses)s, día %(date)s") % {
            "houses": ", ".join(chicken_houses),
            "date": record.date.strftime("%d/%m"),
        }
        production_payload = {
            "id": record.pk,
            "label": label,
            "farm": farms_label,
            "destination": ", ".join(destination_names.keys()),
            "cartons": confirmed_cartons,
            "rooms": room_names,
            "production_date_iso": record.date.isoformat(),
            "production_date_label": date_format(record.date, "DATE_FORMAT"),
        }
        total_cartons += confirmed_cartons
        productions.append(production_payload)

    productions.sort(key=lambda item: (item["production_date_iso"], item["id"]))
    default_expected_date = today + timedelta(days=1)
    default_transporter_id = transporters[0]["id"] if transporters else None
    return {
        "title": _("Producciones listas para transporte"),
        "pending_count": len(productions),
        "total_cartons": total_cartons,
        "productions": productions,
        "transporters": transporters,
        "default_transporter_id": default_transporter_id,
        "default_expected_date_iso": default_expected_date.isoformat(),
        "default_expected_date_label": date_format(default_expected_date, "DATE_FORMAT"),
        "instructions": _(""),
    }

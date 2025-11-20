from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _

from personal.models import UserProfile
from production.models import EggClassificationBatch
from production.services.internal_transport import TransportBatchSnapshot, build_transport_snapshot

TRANSPORT_PROGRESS_STEPS = [
    {"id": "verified", "label": _("Verificado")},
    {"id": "loaded", "label": _("Cargado")},
    {"id": "departed", "label": _("Iniciar transporte")},
    {"id": "arrival", "label": _("En destino")},
    {"id": "unloading", "label": _("Descargando")},
    {"id": "completed", "label": _("Completado")},
]

TRANSPORT_CHECKPOINTS = []

VERIFICATION_CHECKPOINTS = [
    _("Anota diferencias en cartones o unidades."),
    _("Escanea QR de trazabilidad antes de firmar."),
]


def _format_manifest_entry(snapshot: TransportBatchSnapshot) -> dict[str, Any]:
    label_parts = [snapshot.farm_name]
    if snapshot.chicken_houses:
        label_parts.append(" · ".join(snapshot.chicken_houses))
    label = " · ".join(label_parts)
    return {
        "id": snapshot.id,
        "label": label,
        "farm": snapshot.farm_name,
        "destination": snapshot.destination_name,
        "houses": snapshot.chicken_houses,
        "rooms": snapshot.rooms,
        "cartons": snapshot.cartons_confirmed,
        "pending_cartons": snapshot.pending_cartons,
        "production_date_iso": snapshot.production_date.isoformat(),
        "production_date_label": date_format(snapshot.production_date, "DATE_FORMAT"),
        "transporter": snapshot.transporter_label,
        "transporter_id": snapshot.transporter_id,
        "expected_date": snapshot.expected_date.isoformat() if snapshot.expected_date else None,
        "expected_date_label": date_format(snapshot.expected_date, "DATE_FORMAT") if snapshot.expected_date else None,
        "confirmed_cartons": snapshot.confirmed_cartons,
    }


def build_transport_stage_payload(*, user: UserProfile | None = None) -> dict[str, Any]:
    statuses = [
        EggClassificationBatch.TransportStatus.AUTHORIZED,
        EggClassificationBatch.TransportStatus.IN_TRANSIT,
    ]
    manifest_snapshot = build_transport_snapshot(statuses=statuses)
    manifest_entries = []
    has_confirmable = False
    for entry in manifest_snapshot:
        formatted = _format_manifest_entry(entry)
        formatted["can_confirm"] = bool(user and entry.transporter_id and user.pk == entry.transporter_id)
        if formatted["can_confirm"]:
            has_confirmable = True
        manifest_entries.append(formatted)
    progress_steps_index = {step["id"]: idx for idx, step in enumerate(TRANSPORT_PROGRESS_STEPS)}
    progress_step_index = -1
    progress_step_id: str | None = None
    for snapshot in manifest_snapshot:
        step_id = snapshot.progress_step
        if not step_id:
            continue
        idx = progress_steps_index.get(step_id)
        if idx is None:
            continue
        if idx > progress_step_index:
            progress_step_index = idx
            progress_step_id = step_id
    origin_farms = {entry.farm_name for entry in manifest_snapshot}
    destination_farms = {entry.destination_name for entry in manifest_snapshot}
    route_origin = _("%(count)s granjas") % {"count": len(origin_farms)} if origin_farms else _("Sin asignar")
    route_destination = (
        _("%(count)s destinos internos") % {"count": len(destination_farms)}
        if destination_farms
        else _("Por confirmar")
    )
    total_cartons = sum((entry.cartons_confirmed for entry in manifest_snapshot), Decimal("0"))
    today = timezone.localdate()
    now = timezone.localtime()

    return {
        "id": "transport",
        "icon": "🚚",
        "title": _("Transporte interno"),
        "tone": "brand",
        "status": "pending" if not manifest_entries else "in_progress",
        "summary": _(
            ""
        ),
        "metrics": [],
        "route": {
            "origin": route_origin,
            "destination": route_destination,
        },
        "manifest": {
            "entries": manifest_entries,
            "total_cartons": total_cartons,
            "updated_at": date_format(now, "DATETIME_FORMAT"),
            "has_confirmable": has_confirmable,
        },
        "progress_steps": TRANSPORT_PROGRESS_STEPS,
        "checkpoints": TRANSPORT_CHECKPOINTS,
        "progress": {
            "step_index": progress_step_index,
            "step_id": progress_step_id,
        },
    }


def build_transport_verification_payload() -> dict[str, Any]:
    statuses = [EggClassificationBatch.TransportStatus.VERIFICATION]
    entries_snapshot = build_transport_snapshot(statuses=statuses)
    entries = []
    for snapshot in entries_snapshot:
        houses_label = ", ".join(snapshot.chicken_houses) or _("Lote")
        entries.append(
            {
                "id": snapshot.id,
                "label": _("%(farm)s · %(houses)s") % {"farm": snapshot.farm_name, "houses": houses_label},
                "cartons": snapshot.cartons_confirmed,
                "destination": snapshot.destination_name,
                "production_date_label": date_format(snapshot.production_date, "DATE_FORMAT"),
                "rooms": snapshot.rooms,
                "verified_cartons": snapshot.verified_cartons,
            }
        )
    total_cartons = sum((entry.cartons_confirmed for entry in entries_snapshot), Decimal("0"))
    total_entries = len(entries)

    return {
        "id": "verification",
        "icon": "📦",
        "title": _("Verificación en acopio"),
        "tone": "sky",
        "status": "pending" if not entries else "in_progress",
        "summary": _("Valida que lo recibido coincide con lo transportado y reporta ajustes en línea."),
        "metrics": [
            {"label": _("Cartones por verificar"), "value": total_cartons, "unit": _("cartones")},
            {"label": _("Producciones"), "value": total_entries, "unit": _("lotes")},
        ],
        "entries": entries,
        "checkpoints": VERIFICATION_CHECKPOINTS,
    }

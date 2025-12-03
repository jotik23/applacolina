from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Iterable, Mapping, Optional, Sequence

from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.models import Q, Sum
from django.db.utils import OperationalError, ProgrammingError
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.text import slugify
from django.utils.translation import gettext as _

from personal.models import UserProfile
from production.models import (
    BirdBatchRoomAllocation,
    ProductionRecord,
    ProductionRoomRecord,
    Room,
    WeightSample,
    WeightSampleSession,
)
from task_manager.models import TaskAssignment, TaskDefinition

from .production_registry import resolve_assignment_for_date

DECIMAL_QUANTIZE = Decimal("0.01")


@dataclass(frozen=True)
class WeightMetrics:
    count: int
    average_grams: Optional[Decimal]
    variance_grams: Optional[Decimal]
    min_grams: Optional[Decimal]
    max_grams: Optional[Decimal]
    uniformity_percent: Optional[Decimal]
    within_tolerance: int
    tolerance_percent: int


@dataclass(frozen=True)
class WeightLocation:
    identifier: str
    room_id: int
    label: str
    farm_name: Optional[str]
    barn_name: Optional[str]
    room_name: Optional[str]
    birds: Optional[int]


@dataclass(frozen=True)
class WeightSessionSnapshot:
    session_id: int
    location_id: str
    room_id: int
    birds: Optional[int]
    entries: tuple[Decimal, ...]
    metrics: WeightMetrics
    created_at: datetime
    updated_at: datetime
    submitted_at: Optional[datetime]
    created_by_display: Optional[str]
    updated_by_display: Optional[str]


@dataclass(frozen=True)
class WeightSessionSummary:
    session_id: int
    barn_label: Optional[str]
    room_label: Optional[str]
    date: date
    average_grams: Optional[Decimal]
    uniformity_percent: Optional[Decimal]
    sample_size: int

    @property
    def label(self) -> str:
        date_label = date_format(self.date, "d M").strip()
        barn = self.barn_label or _("Sin galpón")
        room = self.room_label or _("Sin salón")
        return f"{barn} · {room} · {date_label}"


@dataclass(frozen=True)
class WeightRegistry:
    date: date
    task_assignment_id: Optional[int]
    task_definition_id: Optional[int]
    production_record_id: Optional[int]
    room_record_ids: Mapping[int, int]
    context_token: str
    unit_label: str
    min_sample_size: int
    uniformity_tolerance_percent: int
    locations: tuple[WeightLocation, ...]
    sessions: tuple[WeightSessionSnapshot, ...]
    recent_sessions: tuple[WeightSessionSummary, ...]
    resume_hint: str

    def location_map(self) -> dict[int, WeightLocation]:
        return {location.room_id: location for location in self.locations}

    def location_by_identifier(self) -> dict[str, WeightLocation]:
        return {location.identifier: location for location in self.locations}


def _decimal_from_value(value: object) -> Decimal:
    if value is None or value == "":
        raise ValidationError(_("Los pesos enviados no pueden estar vacíos."))
    try:
        numeric = Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise ValidationError(_("Uno de los pesos enviados no es válido."))
    if numeric <= 0:
        raise ValidationError(_("Los pesos deben ser mayores a cero."))
    return numeric.quantize(DECIMAL_QUANTIZE)


def _compute_metrics(entries: Sequence[Decimal], tolerance_percent: int) -> WeightMetrics:
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


def _resolve_rooms_for_assignment(assignment) -> Sequence[Room]:
    position = assignment.position
    if not position:
        return []
    rooms = list(position.rooms.all())
    if rooms:
        return rooms
    if position.chicken_house_id:
        return list(Room.objects.filter(chicken_house=position.chicken_house).order_by("name"))
    return []


def _birds_per_room(rooms: Sequence[Room]) -> dict[int, int]:
    if not rooms:
        return {}
    aggregates = (
        BirdBatchRoomAllocation.objects.filter(room__in=rooms)
        .values("room_id")
        .annotate(total=Sum("quantity"))
    )
    return {row["room_id"]: int(row["total"] or 0) for row in aggregates}


def _serialize_user_display(user) -> Optional[str]:
    if not user:
        return None
    if hasattr(user, "get_full_name"):
        value = user.get_full_name() or user.get_username()
    else:
        value = getattr(user, "username", None)
    return value.strip() if value else None


def build_weight_registry(
    *,
    user: Optional[UserProfile],
    reference_date: Optional[date] = None,
    session_token: Optional[str] = None,
) -> Optional[WeightRegistry]:
    if not user or not getattr(user, "is_authenticated", False):
        return None

    if not user.has_perm("task_manager.view_mini_app_weight_registry_card"):
        return None

    target_date = reference_date or UserProfile.colombia_today()
    assignment = resolve_assignment_for_date(user=user, target_date=target_date)
    if not assignment or not assignment.position:
        return None

    supports_assignment_fk = _weight_session_assignment_column_exists()

    weight_assignment = _resolve_bird_weight_assignment(
        user=user,
        target_date=target_date,
        position_id=assignment.position_id,
    )
    if not weight_assignment:
        return None

    rooms = _resolve_rooms_for_assignment(assignment)
    locations: list[WeightLocation] = []
    birds_map = _birds_per_room(rooms)
    room_ids = [room.pk for room in rooms]
    room_record_map: dict[int, int] = {}

    for room in rooms:
        chicken_house = room.chicken_house
        farm = chicken_house.farm if chicken_house else None
        identifier = f"room-{room.pk}"
        label_parts = []
        if farm:
            label_parts.append(farm.name)
        if chicken_house:
            label_parts.append(chicken_house.name)
        label_parts.append(room.name)
        label = " · ".join(label_parts)
        locations.append(
            WeightLocation(
                identifier=identifier,
                room_id=room.pk,
                label=label,
                farm_name=farm.name if farm else None,
                barn_name=chicken_house.name if chicken_house else None,
                room_name=room.name,
                birds=birds_map.get(room.pk),
            )
        )

    select_related_fields = [
        "room",
        "room__chicken_house",
        "room__chicken_house__farm",
        "production_record",
        "created_by",
        "updated_by",
    ]
    if supports_assignment_fk:
        select_related_fields.append("task_assignment")

    sessions_base_queryset = (
        WeightSampleSession.objects.select_related(*select_related_fields)
        .prefetch_related("samples")
        .filter(date=target_date, room__in=rooms)
        .order_by("room__name")
    )

    if supports_assignment_fk:
        sessions = list(
            sessions_base_queryset.filter(task_assignment_id=weight_assignment.pk)
        )
        if not sessions:
            sessions = list(
                sessions_base_queryset.filter(task_assignment__isnull=True)
            )
    else:
        sessions = list(sessions_base_queryset)

    location_by_room = {location.room_id: location for location in locations}
    session_snapshots: list[WeightSessionSnapshot] = []

    if weight_assignment.production_record_id and room_ids:
        room_records = ProductionRoomRecord.objects.filter(
            production_record_id=weight_assignment.production_record_id,
            room_id__in=room_ids,
        ).values("room_id", "id")
        room_record_map = {row["room_id"]: row["id"] for row in room_records}

    for session in sessions:
        location = location_by_room.get(session.room_id)
        if not location:
            continue
        entries = tuple(sample.grams for sample in session.samples.all())
        metrics = _compute_metrics(entries, session.tolerance_percent or 10)
        session_snapshots.append(
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

    recent_select_related = ["room", "room__chicken_house"]
    if supports_assignment_fk:
        recent_select_related.append("task_assignment")

    recent_sessions_queryset = (
        WeightSampleSession.objects.select_related(*recent_select_related)
        .prefetch_related("samples")
        .filter(room__in=rooms)
        .exclude(date__lt=target_date - timedelta(days=30))
    )
    if supports_assignment_fk:
        recent_sessions_queryset = recent_sessions_queryset.filter(
            Q(task_assignment_id=weight_assignment.pk)
            | Q(task_assignment__task_definition_id=weight_assignment.task_definition_id)
            | Q(task_assignment__isnull=True)
        )
    recent_sessions_queryset = recent_sessions_queryset.order_by("-submitted_at", "-updated_at")[:5]
    recent_summaries: list[WeightSessionSummary] = []
    for session in recent_sessions_queryset:
        metrics = _compute_metrics(tuple(sample.grams for sample in session.samples.all()), session.tolerance_percent)
        recent_summaries.append(
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

    context_token = _generate_weight_registry_context_token(
        user=user,
        assignment=weight_assignment,
        session_token=session_token,
        target_date=target_date,
    )

    return WeightRegistry(
        date=target_date,
        task_assignment_id=weight_assignment.pk,
        task_definition_id=weight_assignment.task_definition_id,
        production_record_id=weight_assignment.production_record_id,
        room_record_ids=room_record_map,
        context_token=context_token,
        unit_label="g",
        min_sample_size=30,
        uniformity_tolerance_percent=10,
        locations=tuple(locations),
        sessions=tuple(session_snapshots),
        recent_sessions=tuple(recent_summaries),
        resume_hint=_("Puedes pausar el registro."),
    )


def _generate_weight_registry_context_token(
    *,
    user: Optional[UserProfile],
    assignment: TaskAssignment,
    session_token: Optional[str],
    target_date: date,
) -> str:
    user_part = str(user.pk) if user and user.pk else "anonymous"
    assignment_part = str(assignment.pk)
    definition_part = str(assignment.task_definition_id)
    date_part = target_date.isoformat()
    session_part = session_token or "legacy-session"
    raw = "|".join((user_part, assignment_part, definition_part, date_part, session_part))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _resolve_bird_weight_assignment(
    *,
    user: Optional[UserProfile],
    target_date: date,
    position_id: Optional[int],
) -> Optional[TaskAssignment]:
    if not user or not getattr(user, "is_authenticated", False):
        return None

    assignments = (
        TaskAssignment.objects.select_related(
            "task_definition",
            "task_definition__position",
            "production_record",
        )
        .filter(
            due_date=target_date,
            task_definition__record_format=TaskDefinition.RecordFormat.BIRD_WEIGHT,
        )
        .order_by("task_definition__pk")
    )

    collaborator_assignments = assignments.filter(collaborator=user)
    if position_id:
        collaborator_assignments = collaborator_assignments.filter(
            Q(task_definition__position_id=position_id) | Q(task_definition__position__isnull=True)
        )
    prioritized = collaborator_assignments.first()
    if prioritized:
        return prioritized

    fallback = assignments.filter(collaborator__isnull=True)
    if position_id:
        fallback = fallback.filter(task_definition__position_id=position_id)
    return fallback.first()


def _resolve_production_record(*, room_id: int, target_date: date) -> Optional[ProductionRecord]:
    return (
        ProductionRecord.objects.filter(
            date=target_date,
            bird_batch__allocations__room_id=room_id,
        )
        .order_by("pk")
        .first()
    )


def _resolve_production_room_record(*, room_id: int, target_date: date) -> Optional[ProductionRoomRecord]:
    return (
        ProductionRoomRecord.objects.select_related("production_record")
        .filter(production_record__date=target_date, room_id=room_id)
        .order_by("pk")
        .first()
    )


def persist_weight_registry(
    *,
    registry: WeightRegistry,
    sessions: Iterable[Mapping[str, object]],
    user: UserProfile,
) -> None:
    if not sessions:
        raise ValidationError(_("Debes enviar al menos un salón con pesos."))

    location_map = registry.location_map()
    locations_by_identifier = registry.location_by_identifier()
    now = timezone.now()
    assignment_id = registry.task_assignment_id
    production_record_id = registry.production_record_id
    room_record_map = dict(registry.room_record_ids or {})
    supports_assignment_fk = _weight_session_assignment_column_exists()

    with transaction.atomic():
        for session_payload in sessions:
            if not isinstance(session_payload, Mapping):
                raise ValidationError(_("Formato de sesión inválido."))

            raw_room_id = session_payload.get("room_id") or session_payload.get("room")
            location = None
            room_key: Optional[int] = None
            if raw_room_id not in (None, ""):
                try:
                    room_key = int(raw_room_id)
                except (TypeError, ValueError):
                    room_key = None
                if room_key is not None:
                    location = location_map.get(room_key)

            location_id = session_payload.get("id") or session_payload.get("location_id")
            if not location and location_id not in (None, ""):
                location_str = str(location_id)
                location = locations_by_identifier.get(location_str)
                if not location:
                    if location_str.startswith("room-"):
                        suffix = location_str.split("room-", 1)[-1]
                        if suffix.isdigit():
                            fallback_key = int(suffix)
                            location = location_map.get(fallback_key)
                    if not location and room_key is None:
                        # Attempt to resolve using slugified labels for legacy identifiers.
                        location_slug_map = {
                            slugify(loc.label): loc for loc in registry.locations if loc.label
                        }
                        fallback_slug = slugify(location_str) if location_str else ""
                        location = location_slug_map.get(fallback_slug)

            if not location:
                raise ValidationError(
                    _("El salón enviado no está asignado a tu posición.")
                )

            entries_raw = session_payload.get("entries") or session_payload.get("entries_grams") or []
            if not isinstance(entries_raw, Iterable):
                raise ValidationError(_("Debes enviar los pesos capturados para cada salón."))

            entries: list[Decimal] = []
            for value in entries_raw:
                if value in (None, ""):
                    continue
                entries.append(_decimal_from_value(value))

            base_queryset = WeightSampleSession.objects.select_for_update().filter(
                date=registry.date,
                room_id=location.room_id,
            )
            session_obj: Optional[WeightSampleSession]
            if supports_assignment_fk:
                session_obj = base_queryset.filter(task_assignment_id=assignment_id).first()
                if session_obj is None:
                    session_obj = base_queryset.filter(task_assignment__isnull=True).first()
            else:
                session_obj = base_queryset.first()

            if not entries and session_obj is None:
                # Nothing to persist for this location.
                continue

            if session_obj is None:
                session_obj = WeightSampleSession(
                    date=registry.date,
                    room_id=location.room_id,
                    created_by=user,
                )
                if supports_assignment_fk:
                    session_obj.task_assignment_id = assignment_id
            else:
                if supports_assignment_fk:
                    if session_obj.task_assignment_id not in (None, assignment_id):
                        raise ValidationError(_("Ya existe un pesaje registrado para otra tarea."))
                    if session_obj.created_by_id is None:
                        session_obj.created_by = user
                    session_obj.task_assignment_id = assignment_id
            session_obj.unit = registry.unit_label
            session_obj.tolerance_percent = registry.uniformity_tolerance_percent
            session_obj.minimum_sample = registry.min_sample_size
            session_obj.birds = location.birds
            session_obj.updated_by = user
            session_obj.submitted_at = now
            target_production_record_id = production_record_id
            target_room_record_id = room_record_map.get(location.room_id)
            resolved_room_record = None

            if target_room_record_id is None:
                resolved_room_record = _resolve_production_room_record(
                    room_id=location.room_id,
                    target_date=registry.date,
                )
                if resolved_room_record:
                    target_room_record_id = resolved_room_record.pk
                    room_record_map[location.room_id] = target_room_record_id
                    if target_production_record_id is None:
                        target_production_record_id = resolved_room_record.production_record_id

            if target_production_record_id is not None:
                session_obj.production_record_id = target_production_record_id
            else:
                production_record = _resolve_production_record(
                    room_id=location.room_id,
                    target_date=registry.date,
                )
                if production_record:
                    session_obj.production_record = production_record
                    target_production_record_id = production_record.pk

            if target_room_record_id is not None:
                session_obj.production_room_record_id = target_room_record_id
            elif target_production_record_id is not None and resolved_room_record is None:
                fallback_room_record = ProductionRoomRecord.objects.filter(
                    production_record_id=target_production_record_id,
                    room_id=location.room_id,
                ).order_by('pk').first()
                if fallback_room_record:
                    session_obj.production_room_record_id = fallback_room_record.pk
                    room_record_map[location.room_id] = fallback_room_record.pk

            metrics = _compute_metrics(entries, session_obj.tolerance_percent or registry.uniformity_tolerance_percent)
            session_obj.sample_size = metrics.count
            session_obj.average_grams = metrics.average_grams
            session_obj.variance_grams = metrics.variance_grams
            session_obj.min_grams = metrics.min_grams
            session_obj.max_grams = metrics.max_grams
            session_obj.uniformity_percent = metrics.uniformity_percent
            session_obj.within_tolerance = metrics.within_tolerance
            session_obj.save()

            session_obj.samples.all().delete()
            if entries:
                WeightSample.objects.bulk_create(
                    [
                        WeightSample(
                            session=session_obj,
                            grams=value,
                            recorded_at=now,
                            recorded_by=user,
                        )
                        for value in entries
                    ]
                )


def _decimal_to_float(value: Optional[Decimal]) -> Optional[float]:
    if value is None:
        return None
    return float(value)


def _serialize_metrics(metrics: WeightMetrics) -> dict[str, object]:
    return {
        "count": metrics.count,
        "average_grams": _decimal_to_float(metrics.average_grams),
        "variance_grams": _decimal_to_float(metrics.variance_grams),
        "min_grams": _decimal_to_float(metrics.min_grams),
        "max_grams": _decimal_to_float(metrics.max_grams),
        "uniformity_percent": _decimal_to_float(metrics.uniformity_percent),
        "within_tolerance": metrics.within_tolerance,
        "tolerance_percent": metrics.tolerance_percent,
    }


def serialize_weight_registry(registry: WeightRegistry) -> dict[str, object]:
    locations_payload = [
        {
            "id": location.identifier,
            "room_id": location.room_id,
            "label": location.label,
            "farm": location.farm_name,
            "barn": location.barn_name,
            "room": location.room_name,
            "birds": location.birds,
        }
        for location in registry.locations
    ]

    sessions_payload = [
        {
            "id": session.location_id,
            "room_id": session.room_id,
            "entries": [float(value) for value in session.entries],
            "metrics": _serialize_metrics(session.metrics),
            "birds": session.birds,
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
            "submitted_at": session.submitted_at.isoformat() if session.submitted_at else None,
            "created_by": session.created_by_display,
            "updated_by": session.updated_by_display,
        }
        for session in registry.sessions
    ]

    recent_payload = [
        {
            "id": summary.session_id,
            "label": summary.label,
            "avg_weight": _decimal_to_float(summary.average_grams),
            "uniformity_percent": _decimal_to_float(summary.uniformity_percent),
            "sample_size": summary.sample_size,
        }
        for summary in registry.recent_sessions
    ]

    return {
        "date": registry.date.isoformat(),
        "date_label": date_format(registry.date, "DATE_FORMAT"),
        "task_assignment_id": registry.task_assignment_id,
        "task_definition_id": registry.task_definition_id,
        "production_record_id": registry.production_record_id,
        "production_room_record_ids": dict(registry.room_record_ids),
        "context_token": registry.context_token,
        "title": _("Pesaje de aves"),
        "subtitle": "",
        "unit_label": registry.unit_label,
        "min_sample_size": registry.min_sample_size,
        "uniformity_tolerance_percent": registry.uniformity_tolerance_percent,
        "locations": locations_payload,
        "sessions": sessions_payload,
        "recent_sessions": recent_payload,
        "resume_hint": registry.resume_hint,
    }


def _weight_session_assignment_column_exists() -> bool:
    """Check if the weight session table already includes the task assignment FK."""

    table_name = WeightSampleSession._meta.db_table
    try:
        with connection.cursor() as cursor:
            description = connection.introspection.get_table_description(cursor, table_name)
    except (ProgrammingError, OperationalError):
        return False

    for column in description:
        column_name = getattr(column, "name", None)
        if column_name is None and isinstance(column, (list, tuple)):
            column_name = column[0]
        if column_name == "task_assignment_id":
            return True
    return False

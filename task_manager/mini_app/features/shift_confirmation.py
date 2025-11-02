from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Optional

from django.db.models import Case, IntegerField, Value, When
from django.utils.formats import date_format

from personal.models import CalendarStatus, ShiftAssignment, UserProfile


_STATUS_PRIORITY = Case(
    When(calendar__status=CalendarStatus.MODIFIED, then=Value(0)),
    When(calendar__status=CalendarStatus.APPROVED, then=Value(1)),
    When(calendar__status=CalendarStatus.DRAFT, then=Value(2)),
    default=Value(3),
    output_field=IntegerField(),
)


@dataclass(frozen=True)
class ShiftConfirmationCard:
    """Immutable payload describing the shift confirmation card."""

    assignment_id: int
    calendar_id: int
    date: date
    greeting_label: str
    date_label: str
    summary_label: str
    category_label: str
    position_label: str
    farm: Optional[str]
    barn: Optional[str]
    rooms: list[str]
    handoff_from: Optional[str]
    handoff_to: Optional[str]
    requires_confirmation: bool
    confirmed: bool
    storage_key: str


@dataclass(frozen=True)
class ShiftConfirmationEmptyCard:
    """Payload for the empty state when the collaborator has no shift assigned."""

    date: date
    greeting_label: str
    headline: str
    body_lines: list[str]
    action_label: str
    storage_key: str


def build_shift_confirmation_card(
    *,
    user: Optional[UserProfile],
    reference_date: Optional[date] = None,
) -> Optional[ShiftConfirmationCard]:
    """Resolve the confirmation card for the given user and date."""

    if not user or not getattr(user, "is_authenticated", False):
        return None

    target_date = reference_date or UserProfile.colombia_today()

    assignment = (
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
            calendar__status__in=(
                CalendarStatus.MODIFIED,
                CalendarStatus.APPROVED,
                CalendarStatus.DRAFT,
            ),
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

    if not assignment:
        return None

    position = assignment.position
    category = position.category
    farm = position.farm.name if position.farm_id else None
    barn = position.chicken_house.name if position.chicken_house_id else None
    rooms = sorted(room.name for room in position.rooms.all())

    handoff_from = _resolve_adjacent_operator(position_id=position.pk, reference_date=target_date, offset=-1)
    handoff_to = _resolve_adjacent_operator(position_id=position.pk, reference_date=target_date, offset=1)

    operator_name = (
        user.get_short_name()
        or user.get_full_name()
        or getattr(user, "get_username", lambda: "")()
        or "Operario"
    )
    operator_name = operator_name.strip()
    greeting_label = f"Hola {operator_name}, hoy {_format_reference_date(target_date)}"

    date_label = date_format(target_date, "DATE_FORMAT")
    category_label = category.display_name if category else position.name
    position_label = position.name
    summary_label = f"{category_label} · {position_label}"
    storage_key = f"miniapp-shift-confirm::{assignment.pk}:{target_date.isoformat()}"

    card = ShiftConfirmationCard(
        assignment_id=assignment.pk,
        calendar_id=assignment.calendar_id,
        date=target_date,
        greeting_label=greeting_label,
        date_label=date_label,
        summary_label=summary_label,
        category_label=category_label,
        position_label=position_label,
        farm=farm,
        barn=barn,
        rooms=rooms,
        handoff_from=handoff_from,
        handoff_to=handoff_to,
        requires_confirmation=True,
        confirmed=False,
        storage_key=storage_key,
    )
    return card


def _resolve_adjacent_operator(*, position_id: int, reference_date: date, offset: int) -> Optional[str]:
    target = reference_date + timedelta(days=offset)

    adjacent = (
        ShiftAssignment.objects.select_related("operator")
        .filter(
            position_id=position_id,
            date=target,
            calendar__status__in=(
                CalendarStatus.MODIFIED,
                CalendarStatus.APPROVED,
                CalendarStatus.DRAFT,
            ),
            calendar__start_date__lte=target,
            calendar__end_date__gte=target,
        )
        .order_by(
            _STATUS_PRIORITY,
            "-calendar__updated_at",
            "-calendar__created_at",
            "calendar_id",
        )
        .first()
    )

    if not adjacent or not adjacent.operator:
        return None

    display_name = adjacent.operator.get_full_name() or adjacent.operator.get_username()
    return display_name.strip()


def _format_reference_date(target_date: date) -> str:
    weekday = date_format(target_date, "l").capitalize()
    day_number = date_format(target_date, "d")
    month_label = date_format(target_date, "M").replace(".", "").lower()
    return f"{weekday} {day_number} de {month_label}"


def serialize_shift_confirmation_card(card: ShiftConfirmationCard) -> dict[str, object]:
    """Serialize the card into simple types suitable for JSON output."""

    payload = asdict(card)
    payload["date"] = card.date.isoformat()
    return payload


def build_shift_confirmation_empty_card(
    *,
    user: Optional[UserProfile],
    reference_date: Optional[date] = None,
) -> Optional[ShiftConfirmationEmptyCard]:
    """Return the empty-state card shown when no shift is assigned."""

    if not user or not getattr(user, "is_authenticated", False):
        return None

    target_date = reference_date or UserProfile.colombia_today()

    operator_name = (
        user.get_short_name()
        or user.get_full_name()
        or getattr(user, "get_username", lambda: "")()
        or "Operario"
    ).strip()
    greeting_label = f"Hola {operator_name}, es {_format_reference_date(target_date)}"
    body_lines = [
        "Hoy no encontramos un turno asignado para ti.",
        "Revisa con tu supervisor y vuelve a ingresar cuando tengas la programación actualizada.",
    ]
    headline = ""
    return ShiftConfirmationEmptyCard(
        date=target_date,
        greeting_label=greeting_label,
        headline=headline,
        body_lines=body_lines,
        action_label="Cerrar sesión",
        storage_key=f"miniapp-shift-empty::{target_date.isoformat()}",
    )


def serialize_shift_confirmation_empty_card(card: ShiftConfirmationEmptyCard) -> dict[str, object]:
    payload = asdict(card)
    payload["date"] = card.date.isoformat()
    return payload

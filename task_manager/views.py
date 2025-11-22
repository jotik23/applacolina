import json
import uuid
from collections import OrderedDict
from dataclasses import dataclass
import re
from decimal import Decimal, InvalidOperation
from datetime import date, datetime, time, timedelta
from enum import Enum
from typing import Any, Iterable, Mapping, Optional, Sequence, cast

from django.conf import settings
from django.contrib import messages
from django.contrib.humanize.templatetags.humanize import intcomma
from django.contrib.auth import login, logout
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Prefetch, Q, QuerySet
from django.http import JsonResponse, QueryDict
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.formats import date_format
from django.utils.text import capfirst, slugify
from django.utils.translation import gettext as _, ngettext
from django.views import View, generic
from django.views.decorators.http import require_POST
from pywebpush import WebPushException, webpush

from administration.forms import SupplierForm
from administration.models import PurchaseRequest, Supplier
from administration.services.purchase_orders import (
    PurchaseOrderPayload,
    PurchaseOrderService,
    PurchaseOrderValidationError,
)
from administration.services.purchase_requests import (
    PurchaseItemPayload,
    PurchaseRequestPayload,
    PurchaseRequestSubmissionService,
    PurchaseRequestValidationError,
)
from administration.services.workflows import (
    PurchaseApprovalDecisionError,
    PurchaseApprovalDecisionService,
)
from applacolina.mixins import StaffRequiredMixin

from personal.models import (
    CalendarStatus,
    DayOfWeek,
    OperatorRestPeriod,
    PositionDefinition,
    RestPeriodStatus,
    ShiftAssignment,
    ShiftType,
    Role,
    UserProfile,
)
from production.models import ChickenHouse, Farm, Room
from production.services.internal_transport import (
    authorize_internal_transport,
    record_transporter_confirmation,
    record_transport_verification,
    update_transport_progress,
)
from task_manager.mini_app.features import (
    build_shift_confirmation_card,
    build_shift_confirmation_empty_card,
    build_production_registry,
    build_feed_plan_card,
    build_night_mortality_registry,
    build_weight_registry,
    build_purchase_requests_overview,
    build_purchase_management_card,
    build_purchase_approval_card,
    build_purchase_request_composer,
    build_transport_queue_payload,
    persist_production_records,
    persist_night_mortality_entries,
    persist_weight_registry,
    serialize_shift_confirmation_card,
    serialize_shift_confirmation_empty_card,
    serialize_production_registry,
    serialize_feed_plan_card,
    serialize_night_mortality_registry,
    serialize_weight_registry,
    serialize_purchase_requests_overview,
    serialize_purchase_management_card,
    serialize_purchase_management_empty_state,
    serialize_purchase_approval_card,
    serialize_purchase_request_composer,
)
from task_manager.mini_app.features.internal_transport import (
    build_transport_stage_payload,
    build_transport_verification_payload,
)
from task_manager.mini_app.features.purchases import (
    PURCHASE_STATUS_THEME as MINI_APP_PURCHASE_STATUS_THEME,
    MAX_PURCHASE_ENTRIES as MINI_APP_PURCHASE_MAX_ITEMS,
    MAX_PURCHASE_REQUEST_ITEMS as MINI_APP_PURCHASE_FORM_MAX_ITEMS,
    RECENT_SUPPLIER_SUGGESTIONS as MINI_APP_PURCHASE_SUPPLIER_LIMIT,
)
from task_manager.services.purchase_notifications import (
    notify_purchase_manager_assignment,
    notify_purchase_returned_for_changes,
    notify_purchase_workflow_result,
)

from .forms import MiniAppAuthenticationForm, MiniAppPushTestForm, TaskDefinitionQuickCreateForm
from .models import (
    MiniAppPushSubscription,
    TaskAssignment,
    TaskAssignmentEvidence,
    TaskCategory,
    TaskDefinition,
    TaskStatus,
)


class MiniAppClient(Enum):
    TELEGRAM = "telegram"
    EMBEDDED = "embedded"
    WEB = "web"


LEGACY_EGG_STAGE_PERMISSION = "task_manager.view_mini_app_egg_stage_cards"

EGG_STAGE_PERMISSION_KEY_BY_STAGE_ID: dict[str, str] = {
    "transport": "egg_stage_transport",
    "verification": "egg_stage_verification",
    "classification": "egg_stage_classification",
    "inspection": "egg_stage_inspection",
    "inventory_ready": "egg_stage_inventory",
    "dispatches": "egg_stage_dispatches",
}

EGG_STAGE_CARD_PERMISSION_MAP: dict[str, str] = {
    "egg_stage_transport": "task_manager.view_mini_app_egg_stage_transport_card",
    "egg_stage_verification": "task_manager.view_mini_app_egg_stage_verification_card",
    "egg_stage_classification": "task_manager.view_mini_app_egg_stage_classification_card",
    "egg_stage_inspection": "task_manager.view_mini_app_egg_stage_inspection_card",
    "egg_stage_inventory": "task_manager.view_mini_app_egg_stage_inventory_card",
    "egg_stage_dispatches": "task_manager.view_mini_app_egg_stage_dispatches_card",
}

EGG_STAGE_CARD_PERMISSION_KEYS = frozenset(EGG_STAGE_CARD_PERMISSION_MAP.keys())

MINI_APP_CARD_PERMISSION_MAP: dict[str, str] = {
    "goals_selection": "task_manager.view_mini_app_goals_selection_card",
    "goals_overview": "task_manager.view_mini_app_goals_overview_card",
    "shift_confirmation": "task_manager.view_mini_app_shift_confirmation_card",
    "production": "task_manager.view_mini_app_production_card",
    "feed_plan": "task_manager.view_mini_app_feed_card",
    "production_summary": "task_manager.view_mini_app_production_summary_card",
    "weight_registry": "task_manager.view_mini_app_weight_registry_card",
    "purchase_overview": "task_manager.view_mini_app_purchase_overview_card",
    "purchase_approval": "task_manager.view_mini_app_purchase_approval_card",
    "purchase_management": "task_manager.view_mini_app_purchase_management_card",
    "pending_classification": "task_manager.view_mini_app_pending_classification_card",
    "transport_queue": "task_manager.view_mini_app_transport_queue_card",
    "dispatch_form": "task_manager.view_mini_app_dispatch_form_card",
    "dispatch_detail": "task_manager.view_mini_app_dispatch_detail_card",
    "daily_roster": "task_manager.view_mini_app_daily_roster_card",
    "leader_review": "task_manager.view_mini_app_leader_review_card",
    "suggestions": "task_manager.view_mini_app_suggestions_card",
    "task": "task_manager.view_mini_app_task_cards",
    "night_mortality": "task_manager.view_mini_app_task_cards",
}
MINI_APP_CARD_PERMISSION_MAP.update(EGG_STAGE_CARD_PERMISSION_MAP)


def _build_mini_app_pwa_config() -> dict[str, object]:
    """Return the runtime config injected into the PWA bootstrap script."""

    return {
        "debug": settings.DEBUG,
        "vapid_public_key": getattr(settings, "WEB_PUSH_PUBLIC_KEY", "") or "",
        "subscription_endpoint": getattr(settings, "WEB_PUSH_SUBSCRIPTION_ENDPOINT", "") or "",
    }


_SESSION_TOKEN_KEY = "mini_app_session_token"


def _resolve_mini_app_session_token(request) -> str:
    token = request.session.get(_SESSION_TOKEN_KEY)
    if not token:
        token = uuid.uuid4().hex
        request.session[_SESSION_TOKEN_KEY] = token
    return str(token)


_TASK_CRITICALITY_TONE_MAP: dict[str, str] = {
    TaskDefinition.CriticalityLevel.LOW: "neutral",
    TaskDefinition.CriticalityLevel.MEDIUM: "brand",
    TaskDefinition.CriticalityLevel.HIGH: "critical",
    TaskDefinition.CriticalityLevel.CRITICAL: "critical",
}

_TASK_CRITICALITY_BADGE_THEME_MAP: dict[str, str] = {
    TaskDefinition.CriticalityLevel.LOW: "neutral",
    TaskDefinition.CriticalityLevel.MEDIUM: "brand",
    TaskDefinition.CriticalityLevel.HIGH: "critical",
    TaskDefinition.CriticalityLevel.CRITICAL: "critical",
}

_TASK_TYPE_BADGE_THEME_MAP: dict[str, str] = {
    TaskDefinition.TaskType.RECURRING: "brand",
    TaskDefinition.TaskType.ONE_TIME: "neutral",
}

_TASK_STATUS_STATE_MAP: dict[str, str] = {
    "pendiente": "pending",
    "en-progreso": "in_progress",
    "progreso": "in_progress",
    "reabierta": "reopened",
    "re-abierta": "reopened",
    "reabierto": "reopened",
    "re-abierto": "reopened",
    "rechazada": "rejected",
    "rechazado": "rejected",
    "rechazo": "rejected",
    "vencido": "overdue",
    "vencida": "overdue",
    "completada": "completed",
    "completado": "completed",
    "ejecutada": "completed",
    "ejecutado": "completed",
}

_TASK_STATE_THEME_MAP: dict[str, str] = {
    "pending": "brand",
    "in_progress": "sky",
    "reopened": "amber",
    "rejected": "rose",
    "overdue": "critical",
    "completed": "emerald",
}

_INCLUDE_STATES = frozenset({"pending", "in_progress", "reopened", "rejected", "overdue", "completed"})

_CALENDAR_STATUS_BADGE_CLASS: dict[str | None, str] = {
    CalendarStatus.DRAFT: "border border-amber-200 bg-amber-50 text-amber-700",
    CalendarStatus.APPROVED: "border border-emerald-200 bg-emerald-50 text-emerald-700",
    CalendarStatus.MODIFIED: "border border-sky-200 bg-sky-50 text-sky-700",
    "manual": "border border-emerald-200 bg-emerald-50 text-emerald-700",
    None: "border border-slate-200 bg-slate-50 text-slate-600",
}

_CALENDAR_STATUS_THEME: dict[str | None, str] = {
    CalendarStatus.DRAFT: "amber",
    CalendarStatus.APPROVED: "emerald",
    CalendarStatus.MODIFIED: "sky",
    "manual": "emerald",
    None: "slate",
}


def _format_compact_date_label(target_date: date) -> str:
    """Return a compact date label like '05 Nov' honoring locale conventions."""

    day_label = date_format(target_date, "d")
    month_label = capfirst(date_format(target_date, "M").strip("."))
    return f"{day_label} {month_label}"


def _build_daily_assignment_day_payload(
    *,
    target_date: date,
    reference_date: date,
    calendar_status_key: Optional[str],
    calendar_status_label: str,
    is_rest: bool,
    role_label: Optional[str],
    farm_label: Optional[str],
    barn_label: Optional[str],
    shift_type_label: Optional[str],
    rest_label: Optional[str],
    rest_notes: Optional[str],
    assignment_notes: Optional[str],
    alerts: Optional[Iterable[str]] = None,
) -> dict[str, object]:
    today = timezone.localdate()
    weekday_label = date_format(target_date, "l").capitalize()
    weekday_short = date_format(target_date, "D").strip(".").capitalize()
    date_label = _format_compact_date_label(target_date)
    location_parts: list[str] = []
    if farm_label:
        location_parts.append(farm_label)
    if barn_label:
        location_parts.append(barn_label)
    location_label = " · ".join(location_parts) if location_parts else None

    badge_class = _CALENDAR_STATUS_BADGE_CLASS.get(calendar_status_key, _CALENDAR_STATUS_BADGE_CLASS[None])
    calendar_theme = _CALENDAR_STATUS_THEME.get(calendar_status_key, _CALENDAR_STATUS_THEME[None])

    payload = {
        "date_iso": target_date.isoformat(),
        "weekday_label": weekday_label,
        "weekday_short_label": weekday_short,
        "date_label": date_label,
        "is_today": target_date == today,
        "calendar_status": calendar_status_label,
        "calendar_status_key": calendar_status_key,
        "calendar_status_badge_class": badge_class,
        "calendar_status_theme": calendar_theme,
        "is_rest": is_rest,
        "role_label": role_label,
        "farm_label": farm_label,
        "barn_label": barn_label,
        "location_label": location_label,
        "shift_type_label": shift_type_label,
        "rest_label": rest_label,
        "rest_notes": rest_notes,
        "assignment_notes": assignment_notes,
        "alerts": [alert for alert in (alerts or []) if alert],
    }
    # Preserve reference to initial comparison day for consumers that rely on it.
    payload["is_reference_day"] = target_date == reference_date
    return payload


def _resolve_operator_daily_assignments(
    *,
    user: Optional[UserProfile],
    reference_date: date,
    max_days: int = 6,
) -> dict[str, object]:
    """Return the next available assignment/rest days for the operator."""

    if not user or not getattr(user, "is_authenticated", False):
        return {
            "current_date_iso": reference_date.isoformat(),
            "initial_index": 0,
            "initial_day": None,
            "days": [],
        }

    search_horizon = reference_date + timedelta(days=45)

    assignments_qs = (
        ShiftAssignment.objects.select_related(
            "calendar",
            "position",
            "position__category",
            "position__farm",
            "position__chicken_house",
        )
        .filter(
            operator=user,
            date__gte=reference_date,
            date__lte=search_horizon,
        )
        .order_by("date", "calendar__start_date", "position__display_order", "position__code")
    )

    assignments_by_day: dict[date, list[ShiftAssignment]] = {}
    for assignment in assignments_qs:
        assignments_by_day.setdefault(assignment.date, []).append(assignment)

    # Consider manual and calendar-generated rests except cancelled ones.
    rest_periods = (
        OperatorRestPeriod.objects.select_related("calendar")
        .filter(
            operator=user,
            start_date__lte=search_horizon,
            end_date__gte=reference_date,
        )
        .exclude(status__in=[RestPeriodStatus.CANCELLED, RestPeriodStatus.EXPIRED])
        .order_by("start_date", "id")
    )

    rest_by_day: dict[date, OperatorRestPeriod] = {}
    for period in rest_periods:
        current = max(period.start_date, reference_date)
        limit = min(period.end_date, search_horizon)
        while current <= limit:
            rest_by_day.setdefault(current, period)
            current += timedelta(days=1)

    days: list[dict[str, object]] = []
    cursor = reference_date
    horizon_limit = search_horizon

    while cursor <= horizon_limit and len(days) < max_days:
        day_assignments = assignments_by_day.get(cursor, [])
        rest_period = rest_by_day.get(cursor)
        if not day_assignments and not rest_period:
            cursor += timedelta(days=1)
            continue

        assignment = day_assignments[0] if day_assignments else None
        alerts: list[str] = []
        calendar = getattr(assignment, "calendar", None)
        calendar_status_key: Optional[str] = getattr(calendar, "status", None)
        calendar_status_label = calendar.get_status_display() if calendar else _("Sin estado")

        if rest_period and not calendar_status_label:
            period_calendar = getattr(rest_period, "calendar", None)
            if period_calendar:
                calendar_status_key = period_calendar.status
                calendar_status_label = period_calendar.get_status_display()
            else:
                calendar_status_key = "manual"
                calendar_status_label = _("Manual")

        if assignment and rest_period:
            alerts.append(
                _("Tienes un descanso y un turno asignado el mismo día. Consulta a tu líder para validar.")
            )

        role_label: Optional[str] = None
        farm_label: Optional[str] = None
        barn_label: Optional[str] = None
        shift_type_label: Optional[str] = None
        assignment_notes: Optional[str] = None

        if assignment:
            position = getattr(assignment, "position", None)
            assignment_notes = assignment.notes or ""
            if position:
                if getattr(position, "name", None):
                    role_label = position.name
                elif getattr(position, "code", None):
                    role_label = position.code
                farm = getattr(position, "farm", None)
                if farm and getattr(farm, "name", None):
                    farm_label = farm.name
                barn = getattr(position, "chicken_house", None)
                if barn and getattr(barn, "name", None):
                    barn_label = barn.name
                category = getattr(position, "category", None)
                if category and getattr(category, "shift_type", None):
                    try:
                        shift_type_label = category.get_shift_type_display()  # type: ignore[attr-defined]
                    except AttributeError:
                        shift_type_label = getattr(category, "shift_type", None)

        rest_label: Optional[str] = None
        rest_notes: Optional[str] = None
        if rest_period:
            status_display = rest_period.get_status_display()
            rest_label = status_display or _("Descanso")
            rest_notes = rest_period.notes or ""
            if not calendar_status_label:
                calendar_status_label = _("Manual")
                calendar_status_key = "manual"

        day_payload = _build_daily_assignment_day_payload(
            target_date=cursor,
            reference_date=reference_date,
            calendar_status_key=calendar_status_key,
            calendar_status_label=calendar_status_label,
            is_rest=bool(rest_period) and not assignment,
            role_label=role_label,
            farm_label=farm_label,
            barn_label=barn_label,
            shift_type_label=shift_type_label,
            rest_label=rest_label,
            rest_notes=rest_notes,
            assignment_notes=assignment_notes,
            alerts=alerts,
        )
        days.append(day_payload)
        cursor += timedelta(days=1)

    for index, day in enumerate(days):
        day["index"] = index

    initial_index = next((day["index"] for day in days if day.get("is_today")), 0) if days else 0
    initial_day = days[initial_index] if days else None

    return {
        "current_date_iso": reference_date.isoformat(),
        "initial_index": initial_index,
        "initial_day": initial_day,
        "days": days,
    }


def _normalize_status_slug(name: Optional[str]) -> str:
    if not name:
        return ""
    return slugify(name, allow_unicode=True)


def _build_assignment_status_info(
    assignment: TaskAssignment,
    reference_date: date,
) -> Optional[dict[str, object]]:
    definition = assignment.task_definition
    if assignment.completed_on:
        state = "completed"
        status_label = _("Completada")
        theme = _TASK_STATE_THEME_MAP.get(state, "emerald")
        due_label = date_format(assignment.due_date, "DATE_FORMAT") if assignment.due_date else ""
        details = _("Marcada %(date)s") % {"date": date_format(assignment.completed_on, "DATE_FORMAT")}
        return {
            "state": state,
            "label": status_label,
            "theme": theme,
            "details": details,
            "overdue_days": None,
            "due_label": due_label,
            "due_date": assignment.due_date,
        }

    status_obj = getattr(definition, "effective_status", None) or definition.status
    status_label = (getattr(status_obj, "name", "") or "").strip()
    status_slug = _normalize_status_slug(status_label)
    state = _TASK_STATUS_STATE_MAP.get(status_slug)
    due_date = assignment.due_date
    tomorrow = reference_date + timedelta(days=1)
    overdue_days = 0
    if due_date and due_date < reference_date:
        overdue_days = (reference_date - due_date).days

    if getattr(definition, "is_overdue", False) and state != "overdue":
        state = "overdue"
        status_label = _("Vencida")

    if state == "pending":
        if due_date:
            if due_date < reference_date:
                state = "overdue"
                status_label = _("Vencida")
            elif due_date > tomorrow:
                return None
    elif state is None:
        if due_date and due_date < reference_date:
            state = "overdue"
            status_label = _("Vencida")
        elif due_date and due_date <= tomorrow:
            state = "pending"
            if not status_label:
                status_label = _("Pendiente")
        elif getattr(definition, "is_overdue", False):
            state = "overdue"
            status_label = _("Vencida")
        else:
            # Status not relevant for the mini-app feed.
            return None

    if state not in _INCLUDE_STATES:
        return None

    theme = _TASK_STATE_THEME_MAP.get(state, "neutral")
    due_label = date_format(due_date, "DATE_FORMAT") if due_date else ""
    details = ""
    if state == "overdue":
        if overdue_days > 0:
            details = ngettext(
                "Retraso de %(count)s día",
                "Retraso de %(count)s días",
                overdue_days,
            ) % {"count": overdue_days}
        elif due_date:
            details = _("Venció %(date)s") % {"date": due_label}

    return {
        "state": state,
        "label": status_label or _("Sin estado"),
        "theme": theme,
        "details": details,
        "overdue_days": overdue_days or None,
        "due_label": due_label,
        "due_date": due_date,
    }


def _serialize_task_assignment(
    assignment: TaskAssignment,
    *,
    reference_date: date,
    status_info: dict[str, object],
    evidence_count: int,
    requires_evidence: bool,
) -> Optional[dict[str, object]]:
    """Serialize a task assignment for use in the mini app feed."""

    definition = assignment.task_definition
    if not definition:
        return None

    description = (definition.description or "").strip()
    if not description:
        description = _("")

    is_completed_today = assignment.completed_on is not None and assignment.completed_on == reference_date

    tone = _TASK_CRITICALITY_TONE_MAP.get(definition.criticality_level, "neutral")

    badges: list[dict[str, str]] = [
        {"label": str(status_info.get("label") or ""), "theme": status_info.get("theme") or "neutral", "kind": "status"}
    ]
    if evidence_count:
        evidence_label = ngettext(
            "%(count)s evidencia",
            "%(count)s evidencias",
            evidence_count,
        ) % {"count": evidence_count}
        badges.insert(0, {"label": evidence_label, "theme": "emerald", "kind": "evidence"})
    elif requires_evidence:
        badges.insert(0, {"label": _("Sin evidencia"), "theme": "critical", "kind": "evidence"})
    due_label = _("Hoy")
    if assignment.due_date and assignment.due_date != reference_date:
        due_label = date_format(assignment.due_date, "DATE_FORMAT")

    due_compact_label = _("Hoy")
    if assignment.due_date:
        due_compact_label = _("Hoy") if assignment.due_date == reference_date else _format_compact_date_label(assignment.due_date)

    meta: list[str] = []

    previous_label: Optional[str] = None
    previous_collaborator = getattr(assignment, "previous_collaborator", None)
    if previous_collaborator and previous_collaborator.pk != assignment.collaborator_id:
        previous_label = previous_collaborator.get_full_name()
        meta.append(_("Asignada inicialmente a %(name)s") % {"name": previous_label})

    evidence_action: list[dict[str, object]] = []
    if requires_evidence:
        evidence_action = [{"label": _("Agregar evidencia"), "action": "evidence", "disabled": False}]

    if is_completed_today:
        actions: list[dict[str, object]] = [{"label": _("Marcar como No Completada"), "action": "reset", "disabled": False}] + evidence_action
    else:
        actions = [{"label": _("Marcar como completada"), "action": "complete", "disabled": False}] + evidence_action

    return {
        "assignment_id": assignment.pk,
        "id": assignment.pk,
        "title": definition.name,
        "tone": tone,
        "badges": badges,
        "description": description,
        "meta": meta,
        "due_compact_label": due_compact_label,
        "reward_points": None,
        "status": {
            "state": status_info.get("state"),
            "label": status_info.get("label"),
            "details": status_info.get("details"),
            "theme": status_info.get("theme"),
            "overdue_days": status_info.get("overdue_days"),
            "due_label": status_info.get("due_label"),
        },
        "requires_evidence": requires_evidence,
        "evidence_count": evidence_count,
        "is_completed_today": is_completed_today,
        "completed_on_iso": assignment.completed_on.isoformat() if is_completed_today else None,
        "due_date_iso": assignment.due_date.isoformat() if assignment.due_date else None,
        "completion_note": assignment.completion_note or "",
        "reassigned_from": previous_label,
        "complete_url": reverse("task_manager:mini-app-task-complete", kwargs={"pk": assignment.pk}),
        "reset_url": reverse("task_manager:mini-app-task-reset", kwargs={"pk": assignment.pk}),
        "evidence_upload_url": reverse("task_manager:mini-app-task-evidence", kwargs={"pk": assignment.pk}),
        "actions": actions,
    }


NIGHT_SHIFT_CUTOFF = time(hour=13, minute=0)


def _normalize_current_time(current_time: Optional[datetime]) -> datetime:
    """Ensure we operate with a timezone-aware datetime in the local timezone."""

    if current_time is None:
        return timezone.localtime()

    if timezone.is_naive(current_time):
        current_time = timezone.make_aware(current_time, timezone=timezone.get_current_timezone())
    else:
        current_time = timezone.localtime(current_time)

    return current_time


def _resolve_task_definition_shift_type(task_definition: TaskDefinition) -> str:
    """Infer the shift type associated with the task definition."""

    position = getattr(task_definition, "position", None)
    if position:
        category = getattr(position, "category", None)
        if category and category.shift_type:
            return category.shift_type
        if position.category_id and not category:
            # Accessing attribute to trigger select_related fallback when missing.
            category = position.category  # pragma: no cover - relies on ORM caching.
            if category and category.shift_type:
                return category.shift_type
    return ShiftType.DAY


def _resolve_shift_window_reference_date(
    task_definition: TaskDefinition,
    *,
    reference_date: date,
    current_time: datetime,
) -> date:
    """Return the due date that corresponds to the active shift window."""

    shift_type = _resolve_task_definition_shift_type(task_definition)
    if shift_type == ShiftType.NIGHT:
        if current_time.time() < NIGHT_SHIFT_CUTOFF:
            return reference_date - timedelta(days=1)
    return reference_date


def _resolve_assignment_reference_date(
    assignment: TaskAssignment,
    *,
    reference_date: date,
    current_time: datetime,
) -> date:
    """Return the reference date that should be used to evaluate the assignment."""

    definition = assignment.task_definition
    if getattr(definition, "is_accumulative", False):
        return reference_date
    return _resolve_shift_window_reference_date(
        definition,
        reference_date=reference_date,
        current_time=current_time,
    )


def _assignment_matches_active_window(
    assignment: TaskAssignment,
    *,
    reference_date: date,
    current_time: datetime,
) -> bool:
    """Return True when the assignment should show up in the mini app for the current shift."""

    definition = assignment.task_definition
    target_date = _resolve_assignment_reference_date(
        assignment,
        reference_date=reference_date,
        current_time=current_time,
    )

    if assignment.completed_on:
        if assignment.due_date:
            return assignment.due_date == target_date
        return assignment.completed_on == target_date

    if getattr(definition, "is_accumulative", False):
        return True

    due_date = assignment.due_date
    if not due_date:
        return True

    return due_date == target_date


def _resolve_daily_task_cards(
    *,
    user: Optional[UserProfile],
    reference_date: date,
    current_time: Optional[datetime] = None,
) -> list[dict[str, object]]:
    """Return the task cards to display in the mini app feed."""

    cards: list[dict[str, object]] = []
    normalized_now = _normalize_current_time(current_time)

    if user and getattr(user, "is_authenticated", False):
        assignments = (
            TaskAssignment.objects.filter(collaborator=user)
            .filter(
                Q(completed_on__isnull=True)
                | Q(completed_on__gte=reference_date - timedelta(days=1))
            )
            .select_related(
                "task_definition",
                "task_definition__category",
                "task_definition__position",
                "task_definition__position__category",
                "task_definition__status",
                "previous_collaborator",
            )
            .prefetch_related("evidences")
            .order_by("due_date", "task_definition__display_order", "task_definition__name", "pk")
        )
        serialized_cards: list[tuple[tuple[int, date, int, int], dict[str, object]]] = []
        for assignment in assignments:
            if not _assignment_matches_active_window(
                assignment,
                reference_date=reference_date,
                current_time=normalized_now,
            ):
                continue
            status_reference_date = _resolve_assignment_reference_date(
                assignment,
                reference_date=reference_date,
                current_time=normalized_now,
            )
            status_info = _build_assignment_status_info(
                assignment,
                reference_date=status_reference_date,
            )
            if not status_info:
                continue
            prefetched = assignment._prefetched_objects_cache.get("evidences") if hasattr(assignment, "_prefetched_objects_cache") else None
            evidence_count = len(prefetched) if prefetched is not None else assignment.evidences.count()
            requires_evidence = (assignment.task_definition.evidence_requirement or TaskDefinition.EvidenceRequirement.NONE) != TaskDefinition.EvidenceRequirement.NONE
            serialized = _serialize_task_assignment(
                assignment,
                reference_date=reference_date,
                status_info=status_info,
                evidence_count=evidence_count,
                requires_evidence=requires_evidence,
            )
            if serialized:
                state_priority_map = {
                    "pending": 0,
                    "in_progress": 1,
                    "reopened": 2,
                    "rejected": 3,
                    "overdue": 4,
                    "completed": 5,
                }
                state = serialized.get("status", {}).get("state") or ""
                priority = state_priority_map.get(state, 9)
                due_date = status_info.get("due_date") or assignment.due_date or reference_date
                serialized_cards.append(((priority, due_date, assignment.task_definition.display_order, assignment.pk), serialized))

        serialized_cards.sort(key=lambda item: item[0])
        for _, payload in serialized_cards:
            cards.append(payload)

        if cards:
            return cards


    return cards


def _resolve_mini_app_client(request) -> MiniAppClient:
    """Infer the client origin (Telegram, embedded app, or browser)."""

    user_agent = (request.META.get("HTTP_USER_AGENT") or "").lower()
    client_hint = (request.GET.get("client") or request.META.get("HTTP_X_LACOLINA_MINIAPP_CLIENT") or "").lower()

    if (
        "telegram" in user_agent
        or request.GET.get("tgWebAppData")
        or request.GET.get("telegram")
        or request.META.get("HTTP_X_TELEGRAM_INIT_DATA")
    ):
        return MiniAppClient.TELEGRAM

    if client_hint in {"mobile", "embedded"}:
        return MiniAppClient.EMBEDDED

    if "miniapp-mobile" in user_agent or "lacolina-miniapp" in user_agent:
        return MiniAppClient.EMBEDDED

    return MiniAppClient.WEB


def _resolve_mini_app_card_permissions(user, *, force_allow: bool = False) -> dict[str, bool]:
    """Return the set of card permissions granted for the current user."""

    if force_allow:
        return {key: True for key in MINI_APP_CARD_PERMISSION_MAP}

    flags = {key: False for key in MINI_APP_CARD_PERMISSION_MAP}
    if not user or not getattr(user, "is_authenticated", False):
        return flags

    legacy_has_access = user.has_perm(LEGACY_EGG_STAGE_PERMISSION)
    for key, permission in MINI_APP_CARD_PERMISSION_MAP.items():
        allowed = user.has_perm(permission)
        if not allowed and legacy_has_access and key in EGG_STAGE_CARD_PERMISSION_KEYS:
            allowed = True
        flags[key] = allowed
    return flags


def _filter_egg_workflow_stages(
    payload: Optional[dict[str, object]], card_permissions: Mapping[str, bool]
) -> None:
    """Filter egg workflow stages according to the granted permissions."""

    if not payload:
        return
    egg_workflow = payload.get("egg_workflow")
    if not isinstance(egg_workflow, dict):
        return
    stages = egg_workflow.get("stages")
    if not isinstance(stages, list):
        return
    filtered: list[dict[str, object]] = []
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        stage_id = stage.get("id")
        if not stage_id:
            continue
        permission_key = EGG_STAGE_PERMISSION_KEY_BY_STAGE_ID.get(stage_id)
        if permission_key and not card_permissions.get(permission_key, False):
            continue
        filtered.append(stage)
    egg_workflow["stages"] = filtered


def _mini_app_json_guard(request) -> Optional[JsonResponse]:
    user = getattr(request, "user", None)
    if not user or not getattr(user, "is_authenticated", False):
        return JsonResponse({"error": _("Debes iniciar sesión en la mini app.")}, status=401)
    if not user.has_perm("task_manager.access_mini_app"):
        return JsonResponse({"error": _("No tienes permisos para la mini app.")}, status=403)
    return None


def _coerce_subscription_expiration(raw_value) -> Optional[datetime]:
    if raw_value in (None, "", "null"):
        return None
    try:
        if isinstance(raw_value, (int, float)):
            timestamp = float(raw_value)
            # Chrome envía milisegundos, navegadores older segundos.
            if timestamp > 1e12:
                timestamp /= 1000.0
            return datetime.fromtimestamp(timestamp, tz=timezone.utc)
        if isinstance(raw_value, str):
            parsed = parse_datetime(raw_value)
            if parsed:
                if timezone.is_naive(parsed):
                    parsed = timezone.make_aware(parsed, timezone=timezone.utc)
                return parsed
    except (ValueError, OSError, OverflowError):
        return None
    return None


def _extract_validation_message(exc: ValidationError) -> str:
    if hasattr(exc, "message_dict") and exc.message_dict:
        parts = []
        for field, messages in exc.message_dict.items():
            joined = ", ".join(str(msg) for msg in messages if msg)
            if joined:
                parts.append(f"{field}: {joined}")
        if parts:
            return " · ".join(parts)
    if hasattr(exc, "messages") and exc.messages:
        return " · ".join(str(msg) for msg in exc.messages if msg)
    return str(exc)


def _coerce_int(value: object) -> Optional[int]:
    if value in (None, "", False):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_decimal(value: object) -> Optional[Decimal]:
    if value in (None, "", False):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _parse_iso_date(value: object) -> Optional[date]:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _normalize_string(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _parse_item_scope_value(raw_value: str) -> dict[str, object]:
    value = (raw_value or "").strip()
    kind = PurchaseRequest.AreaScope.COMPANY
    farm_id: Optional[int] = None
    house_id: Optional[int] = None
    error: Optional[str] = None
    if not value or value == PurchaseRequest.AreaScope.COMPANY:
        kind = PurchaseRequest.AreaScope.COMPANY
    else:
        match = re.match(r"^(farm|chicken_house):(\d+)$", value)
        if not match:
            error = _("Selecciona un área válida.")
        else:
            target_kind = match.group(1)
            target_id = int(match.group(2))
            if target_kind == "farm":
                kind = PurchaseRequest.AreaScope.FARM
                farm_id = target_id
            else:
                kind = PurchaseRequest.AreaScope.CHICKEN_HOUSE
                house_id = target_id
    return {
        "kind": kind,
        "farm_id": farm_id,
        "chicken_house_id": house_id,
        "error": error,
        "value": value if value else PurchaseRequest.AreaScope.COMPANY,
    }


def _build_mini_app_purchase_request_payload(
    data: Mapping[str, object],
    *,
    user: UserProfile,
) -> tuple[Optional[PurchaseRequestPayload], dict[str, list[str]], dict[int, dict[str, list[str]]]]:
    summary = _normalize_string(data.get("summary") or data.get("name"))
    notes = _normalize_string(data.get("notes"))
    expense_type_id = _coerce_int(data.get("expense_type_id"))
    supplier_id = _coerce_int(data.get("supplier_id"))
    assigned_manager_id = _coerce_int(data.get("assigned_manager_id")) or getattr(user, "pk", None)
    support_document_type_id = _coerce_int(data.get("support_document_type_id"))
    purchase_id = _coerce_int(data.get("purchase_id"))
    scope_batch_code = _normalize_string(data.get("scope_batch_code"))
    requested_date = _parse_iso_date(data.get("requested_date")) or timezone.localdate()
    field_errors: dict[str, list[str]] = {}
    item_errors: dict[int, dict[str, list[str]]] = {}

    if not summary:
        field_errors.setdefault("summary", []).append(_("Ingresa el nombre del requerimiento."))
    if not expense_type_id:
        field_errors.setdefault("expense_type_id", []).append(_("Selecciona una categoría."))
    if not supplier_id:
        field_errors.setdefault("supplier_id", []).append(_("Selecciona un tercero."))
    items_raw = data.get("items")
    if not isinstance(items_raw, Sequence):
        items_raw = []
    items_list = list(items_raw)[:MINI_APP_PURCHASE_FORM_MAX_ITEMS]
    if not items_list:
        field_errors.setdefault("items", []).append(_("Agrega al menos un ítem a la solicitud."))
    elif len(items_raw) > MINI_APP_PURCHASE_FORM_MAX_ITEMS:
        field_errors.setdefault("items", []).append(
            _("Solo puedes registrar hasta %(count)s ítems por solicitud.") % {"count": MINI_APP_PURCHASE_FORM_MAX_ITEMS}
        )

    item_payloads: list[PurchaseItemPayload] = []
    for index, raw_row in enumerate(items_list):
        row = raw_row if isinstance(raw_row, Mapping) else {}
        row_errors: dict[str, list[str]] = {}
        description = _normalize_string(row.get("description"))
        product_id = _coerce_int(row.get("product_id"))
        quantity_value = _coerce_decimal(row.get("quantity"))
        unit_value_input = row.get("unit_value", row.get("estimated_amount"))
        estimated_amount = _coerce_decimal(unit_value_input)
        item_id = _coerce_int(row.get("id"))
        scope_value_raw = _normalize_string(row.get("scope_value") or row.get("scope"))
        scope_selection = _parse_item_scope_value(scope_value_raw)

        if not description and not product_id:
            row_errors.setdefault("description", []).append(_("Describe el ítem o selecciona un producto."))
        if quantity_value is None or quantity_value <= Decimal("0"):
            row_errors.setdefault("quantity", []).append(_("Ingresa una cantidad válida."))
        if estimated_amount is None or estimated_amount < Decimal("0"):
            row_errors.setdefault("estimated_amount", []).append(_("Ingresa un valor unitario válido."))
        if scope_selection["error"]:
            row_errors.setdefault("scope", []).append(str(scope_selection["error"]))
        if scope_selection["kind"] == PurchaseRequest.AreaScope.FARM and not scope_selection["farm_id"]:
            row_errors.setdefault("scope", []).append(_("Selecciona la granja para el ítem."))
        if scope_selection["kind"] == PurchaseRequest.AreaScope.CHICKEN_HOUSE and not scope_selection["chicken_house_id"]:
            row_errors.setdefault("scope", []).append(_("Selecciona el galpón para el ítem."))

        if row_errors:
            item_errors[index] = row_errors
            continue

        farm_id = scope_selection["farm_id"]
        house_id = scope_selection["chicken_house_id"]
        if scope_selection["kind"] == PurchaseRequest.AreaScope.CHICKEN_HOUSE and house_id and not farm_id:
            house = ChickenHouse.objects.select_related("farm").filter(pk=house_id).first()
            if house and house.farm_id:
                farm_id = house.farm_id
            else:
                row_errors.setdefault("scope", []).append(_("Selecciona un galpón válido."))
                item_errors[index] = row_errors
                continue

        item_payloads.append(
            PurchaseItemPayload(
                id=item_id,
                description=description,
                quantity=quantity_value,
                estimated_amount=estimated_amount,
                product_id=product_id,
                scope_area=scope_selection["kind"],
                scope_farm_id=farm_id,
                scope_chicken_house_id=house_id if scope_selection["kind"] == PurchaseRequest.AreaScope.CHICKEN_HOUSE else None,
            )
        )

    if not item_payloads and "items" not in field_errors:
        field_errors.setdefault("items", []).append(_("Agrega al menos un ítem a la solicitud."))

    if field_errors or item_errors:
        return None, field_errors, item_errors

    payload = PurchaseRequestPayload(
        purchase_id=purchase_id,
        summary=summary,
        notes=notes,
        expense_type_id=expense_type_id,
        support_document_type_id=support_document_type_id,
        supplier_id=supplier_id,
        items=item_payloads,
        scope_batch_code=scope_batch_code,
        assigned_manager_id=assigned_manager_id,
        requested_date=requested_date,
    )
    return payload, field_errors, item_errors


def _build_mini_app_order_payload(
    data: Mapping[str, object],
    *,
    purchase_id: int,
) -> tuple[Optional[PurchaseOrderPayload], dict[str, list[str]]]:
    purchase_date_raw = _normalize_string(data.get("purchase_date"))
    delivery_condition = _normalize_string(data.get("delivery_condition"))
    shipping_eta_raw = _normalize_string(data.get("shipping_eta"))
    shipping_notes = _normalize_string(data.get("shipping_notes"))
    payment_condition = _normalize_string(data.get("payment_condition"))
    payment_method = _normalize_string(data.get("payment_method"))
    supplier_account_holder_id = _normalize_string(data.get("supplier_account_holder_id"))
    supplier_account_holder_name = _normalize_string(data.get("supplier_account_holder_name"))
    supplier_account_type = _normalize_string(data.get("supplier_account_type"))
    supplier_account_number = _normalize_string(data.get("supplier_account_number"))
    supplier_bank_name = _normalize_string(data.get("supplier_bank_name"))

    purchase_date = _parse_iso_date(purchase_date_raw)
    shipping_eta = _parse_iso_date(shipping_eta_raw)
    field_errors: dict[str, list[str]] = {}
    if not purchase_id:
        field_errors.setdefault("non_field", []).append(_("Selecciona una solicitud válida para gestionar."))
    if not purchase_date_raw:
        field_errors.setdefault("purchase_date", []).append(_("Selecciona la fecha de compra."))
    elif purchase_date is None:
        field_errors.setdefault("purchase_date", []).append(_("Ingresa una fecha válida (AAAA-MM-DD)."))

    delivery_values = set(PurchaseRequest.DeliveryCondition.values)
    if delivery_condition and delivery_condition not in delivery_values:
        field_errors.setdefault("delivery_condition", []).append(_("Selecciona una condición de entrega válida."))
    if not delivery_condition:
        delivery_condition = PurchaseRequest.DeliveryCondition.IMMEDIATE
    if delivery_condition == PurchaseRequest.DeliveryCondition.SHIPPING:
        if not shipping_eta_raw:
            field_errors.setdefault("shipping_eta", []).append(_("Ingresa la fecha estimada de llegada."))
        elif shipping_eta is None:
            field_errors.setdefault("shipping_eta", []).append(_("Fecha estimada inválida."))

    payment_condition_values = set(PurchaseRequest.PaymentCondition.values)
    if payment_condition and payment_condition not in payment_condition_values:
        field_errors.setdefault("payment_condition", []).append(_("Selecciona una condición de pago válida."))
    if not payment_condition:
        field_errors.setdefault("payment_condition", []).append(_("Selecciona una condición de pago."))

    payment_method_values = set(PurchaseRequest.PaymentMethod.values)
    if payment_method and payment_method not in payment_method_values:
        field_errors.setdefault("payment_method", []).append(_("Selecciona un medio de pago válido."))
    if not payment_method:
        field_errors.setdefault("payment_method", []).append(_("Selecciona un medio de pago."))

    require_bank_data = payment_method == PurchaseRequest.PaymentMethod.TRANSFER
    account_types = {choice for choice, _ in Supplier.ACCOUNT_TYPE_CHOICES}
    if require_bank_data:
        if not supplier_account_holder_name:
            field_errors.setdefault("supplier_account_holder_name", []).append(_("Ingresa el titular de la cuenta."))
        if not supplier_account_holder_id:
            field_errors.setdefault("supplier_account_holder_id", []).append(_("Ingresa la identificación del titular."))
        if not supplier_account_number:
            field_errors.setdefault("supplier_account_number", []).append(_("Ingresa el número de cuenta."))
        if not supplier_bank_name:
            field_errors.setdefault("supplier_bank_name", []).append(_("Ingresa el banco."))
        if supplier_account_type not in account_types:
            field_errors.setdefault("supplier_account_type", []).append(_("Selecciona un tipo de cuenta válido."))
    elif supplier_account_type and supplier_account_type not in account_types:
        field_errors.setdefault("supplier_account_type", []).append(_("Selecciona un tipo de cuenta válido."))

    if field_errors or not purchase_id:
        return None, field_errors

    payload = PurchaseOrderPayload(
        purchase_id=purchase_id,
        purchase_date=purchase_date or timezone.localdate(),
        delivery_condition=delivery_condition,
        shipping_eta=shipping_eta if delivery_condition == PurchaseRequest.DeliveryCondition.SHIPPING else None,
        shipping_notes=shipping_notes if delivery_condition == PurchaseRequest.DeliveryCondition.SHIPPING else "",
        payment_condition=payment_condition,
        payment_method=payment_method,
        supplier_account_holder_id=supplier_account_holder_id,
        supplier_account_holder_name=supplier_account_holder_name,
        supplier_account_type=supplier_account_type,
        supplier_account_number=supplier_account_number,
        supplier_bank_name=supplier_bank_name,
    )
    return payload, field_errors


def _format_currency_label(amount: Decimal, currency: str) -> str:
    symbol = _resolve_currency_symbol(currency)
    amount = (amount or Decimal("0.00")).quantize(Decimal("0.01"))
    return f"{symbol} {amount:,.2f}"


def _resolve_currency_symbol(currency: Optional[str]) -> str:
    if not currency:
        return "$"
    code = currency.upper()
    if code == "COP":
        return "$"
    return code


def _build_purchase_overview_payload(user: UserProfile) -> Optional[dict[str, object]]:
    if not user.has_perm("task_manager.view_mini_app_purchase_overview_card"):
        return None
    overview_card = build_purchase_requests_overview(user=user)
    if not overview_card:
        return None
    return serialize_purchase_requests_overview(overview_card)


def _build_purchase_request_composer_payload(user: UserProfile, request) -> Optional[dict[str, object]]:
    if not user.has_perm("task_manager.view_mini_app_purchase_overview_card"):
        return None
    composer = build_purchase_request_composer(user=user)
    if not composer:
        return None
    payload = serialize_purchase_request_composer(composer)
    payload["submit_url"] = reverse("task_manager:mini-app-purchase-request")
    payload["supplier_search_url"] = reverse("task_manager:mini-app-purchase-suppliers")
    payload["supplier_create_url"] = reverse("task_manager:mini-app-purchase-suppliers-create")
    return payload


def _build_purchase_management_payload(user: UserProfile, request) -> Optional[dict[str, object]]:
    if not user.has_perm("task_manager.view_mini_app_purchase_management_card"):
        return None
    management_card = build_purchase_management_card(user=user)
    if management_card:
        payload = serialize_purchase_management_card(management_card)
        payload["request_modification_url"] = reverse(
            "task_manager:mini-app-purchase-request-modify",
            kwargs={"pk": management_card.purchase_id},
        )
        payload["finalize_url"] = reverse(
            "task_manager:mini-app-purchase-finalize",
            kwargs={"pk": management_card.purchase_id},
        )
        payload["order_url"] = reverse(
            "task_manager:mini-app-purchase-order",
            kwargs={"pk": management_card.purchase_id},
        )
        return payload
    return serialize_purchase_management_empty_state()


def _build_purchase_approval_payload(user: UserProfile, request) -> Optional[dict[str, object]]:
    if not user.has_perm("task_manager.view_mini_app_purchase_approval_card"):
        return None
    approval_card = build_purchase_approval_card(user=user)
    if not approval_card:
        return None
    payload = serialize_purchase_approval_card(approval_card)
    for entry in payload.get("entries", []):
        entry["decision_url"] = reverse(
            "task_manager:mini-app-purchase-approval",
            kwargs={"pk": entry.get("id")},
        )
    return payload


def _serialize_purchase_summary(purchase: PurchaseRequest) -> dict[str, object]:
    return {
        "id": purchase.pk,
        "code": purchase.timeline_code,
        "name": purchase.name,
        "status": purchase.status,
        "status_label": purchase.get_status_display(),
        "status_theme": MINI_APP_PURCHASE_STATUS_THEME.get(purchase.status, "slate"),
        "amount_label": _format_currency_label(purchase.estimated_total or Decimal("0.00"), purchase.currency or "COP"),
    }


def _get_user_assignment_for_mini_app(pk: int, *, user: UserProfile) -> TaskAssignment:
    queryset = (
        TaskAssignment.objects.select_related(
            "task_definition",
            "task_definition__status",
        )
        .prefetch_related("evidences")
        .filter(collaborator=user)
    )
    return get_object_or_404(queryset, pk=pk)


def _resolve_primary_group_label(user: UserProfile) -> Optional[str]:
    """Return the name of the first group associated to the user, if any."""

    if not hasattr(user, "groups"):
        return None

    group = user.groups.order_by("name").first()
    return group.name if group else None


def _build_user_initials(user: UserProfile) -> str:
    """Return a short set of initials suitable for compact avatars."""

    initials: list[str] = []
    first_name = (user.nombres or "").strip().split()
    last_name = (user.apellidos or "").strip().split()
    if first_name:
        initials.append(first_name[0][:1])
    if last_name:
        initials.append(last_name[0][:1])
    if not initials and user.nombres:
        initials.append(user.nombres[:1])
    if not initials and user.apellidos:
        initials.append(user.apellidos[:1])
    if not initials and user.cedula:
        initials.append(user.cedula[:2])
    normalized = "".join(initials).upper()
    return normalized or "?"


_ROLE_PRIORITY_MAP = {choice: index for index, (choice, _label) in enumerate(Role.RoleName.choices)}


def _resolve_primary_role_label(user: UserProfile) -> tuple[str, str]:
    """Return the primary role key and label associated with the collaborator."""

    try:
        role = user.roles.order_by("name").first()
    except Exception:
        role = None
    if role:
        return role.name, role.get_name_display()
    group_label = _resolve_primary_group_label(user)
    if group_label:
        slug = slugify(group_label) or "grupo"
        return f"group:{slug}", group_label
    return "unassigned", _("Sin rol asignado")


def build_task_manager_tab_url(
    request,
    *,
    tab: str,
    params: Optional[Mapping[str, object]] = None,
    anchor: Optional[str] = None,
) -> str:
    """Return a URL pointing to the requested tab while preserving query params."""

    query = request.GET.copy()
    query._mutable = True
    query["tm_tab"] = tab
    if params:
        for key, value in params.items():
            if value is None:
                query.pop(key, None)
                continue
            query[key] = str(value)
    querystring = query.urlencode()
    base_url = request.path
    fragment = anchor or f"#tm-{tab}"
    return f"{base_url}?{querystring}{fragment}" if querystring else f"{base_url}{fragment}"


def _build_daily_report_retained_params(request) -> list[dict[str, str]]:
    """Return GET parameters that should survive report form submissions."""

    retained: list[dict[str, str]] = []
    excluded = {"tm_tab", "tm_report_date"}
    for key, values in request.GET.lists():
        if key in excluded:
            continue
        for value in values:
            retained.append({"key": key, "value": value})
    return retained


def _compute_completion_percent(metrics: Mapping[str, int]) -> int:
    total = metrics.get("total") or 0
    if not total:
        return 0
    completed = metrics.get("completed") or 0
    return int(round((completed / total) * 100))


def _resolve_progress_theme(metrics: Mapping[str, int]) -> str:
    if metrics.get("overdue"):
        return "critical"
    if metrics.get("pending"):
        return "brand"
    if metrics.get("completed"):
        return "emerald"
    return "neutral"


def build_daily_assignment_report(
    request,
    *,
    target_date: date,
) -> dict[str, object]:
    """Return aggregated assignment information for the selected day."""

    normalized_date = target_date or timezone.localdate()
    roles_prefetch = Prefetch(
        "collaborator__roles",
        queryset=Role.objects.only("id", "name").order_by("name"),
    )
    assignments = (
        TaskAssignment.objects.filter(due_date=normalized_date, collaborator__isnull=False)
        .select_related(
            "task_definition",
            "task_definition__status",
            "task_definition__category",
            "collaborator",
        )
        .prefetch_related(roles_prefetch)
        .order_by(
            "collaborator__apellidos",
            "collaborator__nombres",
            "task_definition__display_order",
            "task_definition__name",
        )
    )

    totals = {"total": 0, "completed": 0, "pending": 0, "overdue": 0}
    role_groups: OrderedDict[str, dict[str, object]] = OrderedDict()
    state_priority = {
        "overdue": 0,
        "pending": 1,
        "in_progress": 1,
        "reopened": 1,
        "rejected": 2,
        "completed": 3,
    }

    for assignment in assignments:
        collaborator = assignment.collaborator
        definition = assignment.task_definition
        if not collaborator or not definition:
            continue
        status_info = _build_assignment_status_info(assignment, reference_date=normalized_date)
        if status_info is None:
            status_info = {
                "state": "pending",
                "label": _("Pendiente"),
                "theme": "brand",
                "details": "",
                "due_label": date_format(assignment.due_date, "DATE_FORMAT") if assignment.due_date else "",
            }
        state = str(status_info.get("state") or "pending")
        theme = str(status_info.get("theme") or "brand")
        bucket = "pending"
        if state == "completed":
            bucket = "completed"
        elif state == "overdue":
            bucket = "overdue"

        role_key, role_label = _resolve_primary_role_label(collaborator)
        role_entry = role_groups.get(role_key)
        if not role_entry:
            role_entry = {
                "key": role_key,
                "label": role_label,
                "priority": _ROLE_PRIORITY_MAP.get(role_key, 99),
                "metrics": {"total": 0, "completed": 0, "pending": 0, "overdue": 0},
                "collaborators": [],
                "_collaborator_map": OrderedDict(),
            }
            role_groups[role_key] = role_entry

        collaborator_map: OrderedDict[int, dict[str, object]] = role_entry["_collaborator_map"]  # type: ignore[index]
        collaborator_entry = collaborator_map.get(collaborator.pk)
        if not collaborator_entry:
            collaborator_entry = {
                "id": collaborator.pk,
                "name": collaborator.get_full_name(),
                "initials": _build_user_initials(collaborator),
                "role_label": role_label,
                "metrics": {"total": 0, "completed": 0, "pending": 0, "overdue": 0},
                "tasks": [],
            }
            collaborator_map[collaborator.pk] = collaborator_entry
            role_entry["collaborators"].append(collaborator_entry)

        for metrics in (totals, role_entry["metrics"], collaborator_entry["metrics"]):
            metrics["total"] += 1
            metrics[bucket] += 1

        task_payload = {
            "id": assignment.pk,
            "name": definition.name,
            "category_label": getattr(definition.category, "name", ""),
            "display_order": definition.display_order or 0,
            "status_label": status_info.get("label") or "",
            "status_details": status_info.get("details") or "",
            "status_state": state,
            "status_theme": theme,
            "requires_evidence": (
                definition.evidence_requirement != TaskDefinition.EvidenceRequirement.NONE
            ),
        }
        collaborator_entry["tasks"].append(task_payload)

    role_list: list[dict[str, object]] = []
    for role_entry in role_groups.values():
        role_entry.pop("_collaborator_map", None)
        collaborators: list[dict[str, object]] = role_entry["collaborators"]
        for collaborator_entry in collaborators:
            collaborator_entry["tasks"].sort(
                key=lambda task: (
                    task.get("display_order", 0),
                    state_priority.get(task.get("status_state"), 99),
                    str(task.get("name") or "").lower(),
                )
            )
            collaborator_entry["metrics"]["progress_percent"] = _compute_completion_percent(
                collaborator_entry["metrics"]
            )
            collaborator_entry["metrics"]["progress_theme"] = _resolve_progress_theme(collaborator_entry["metrics"])
        collaborators.sort(key=lambda collaborator: collaborator["name"])
        role_entry["metrics"]["progress_percent"] = _compute_completion_percent(role_entry["metrics"])
        role_entry["metrics"]["progress_theme"] = _resolve_progress_theme(role_entry["metrics"])
        role_list.append(role_entry)

    role_list.sort(key=lambda entry: (entry.get("priority", 99), entry.get("label", "")))
    totals["progress_percent"] = _compute_completion_percent(totals)
    totals["progress_theme"] = _resolve_progress_theme(totals)

    prev_date = normalized_date - timedelta(days=1)
    next_date = normalized_date + timedelta(days=1)
    today = timezone.localdate()
    nav_anchor = "#tm-reporte"
    nav = {
        "previous": {
            "date_iso": prev_date.isoformat(),
            "label": date_format(prev_date, "DATE_FORMAT"),
            "url": build_task_manager_tab_url(
                request,
                tab="reporte",
                params={"tm_report_date": prev_date.isoformat()},
                anchor=nav_anchor,
            ),
        },
        "next": {
            "date_iso": next_date.isoformat(),
            "label": date_format(next_date, "DATE_FORMAT"),
            "url": build_task_manager_tab_url(
                request,
                tab="reporte",
                params={"tm_report_date": next_date.isoformat()},
                anchor=nav_anchor,
            ),
        },
        "today": {
            "date_iso": today.isoformat(),
            "label": date_format(today, "DATE_FORMAT"),
            "url": build_task_manager_tab_url(
                request,
                tab="reporte",
                params={"tm_report_date": today.isoformat()},
                anchor=nav_anchor,
            ),
        },
    }

    return {
        "date": normalized_date,
        "date_iso": normalized_date.isoformat(),
        "date_label": date_format(normalized_date, "DATE_FORMAT"),
        "weekday_label": date_format(normalized_date, "l").capitalize(),
        "nav": nav,
        "totals": totals,
        "role_groups": role_list,
        "has_data": bool(role_list),
        "retained_params": _build_daily_report_retained_params(request),
    }


class TaskManagerHomeView(StaffRequiredMixin, generic.TemplateView):
    """Render a placeholder landing page for the task manager module."""

    template_name = "task_manager/index.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault("task_definition_form", TaskDefinitionQuickCreateForm())
        categories = TaskCategory.objects.filter(is_active=True).order_by("name")
        statuses = TaskStatus.objects.filter(is_active=True).order_by("name")
        context["task_manager_categories"] = categories
        context["task_manager_statuses"] = statuses
        allowed_tabs = {"tareas", "reporte"}
        active_tab = self.request.GET.get("tm_tab") or "tareas"
        if active_tab not in allowed_tabs:
            active_tab = "tareas"
        context["task_manager_active_tab"] = active_tab
        context["task_manager_active_submenu"] = "tasks" if active_tab == "tareas" else "daily-report"

        filters = build_task_definition_filters(self.request.GET)
        defaults = TaskDefinitionFilters()

        status_groups = build_status_filter_groups(statuses)
        status_value, status_label = ensure_filter_selection(filters, "status", status_groups, defaults.status)
        category_groups = build_category_filter_groups(categories)
        category_value, category_label = ensure_filter_selection(
            filters, "category", category_groups, defaults.category
        )
        mandatory_groups = build_mandatory_filter_groups()
        mandatory_value, mandatory_label = ensure_filter_selection(
            filters, "mandatory", mandatory_groups, defaults.mandatory
        )
        criticality_groups = build_criticality_filter_groups()
        criticality_value, criticality_label = ensure_filter_selection(
            filters, "criticality", criticality_groups, defaults.criticality
        )
        scope_groups = build_scope_filter_groups()
        scope_value, scope_label = ensure_filter_selection(filters, "scope", scope_groups, defaults.scope)
        responsible_groups = build_responsible_filter_groups()
        responsible_value, responsible_label = ensure_filter_selection(
            filters, "responsible", responsible_groups, defaults.responsible
        )
        highlight_raw = self.request.GET.get("tm_task")
        highlight_id: Optional[int] = None
        if highlight_raw is not None:
            try:
                highlight_id = int(highlight_raw)
            except (TypeError, ValueError):
                highlight_id = None

        queryset = get_task_definition_queryset(filters)
        paginator = Paginator(queryset, 400)
        page_number = self.request.GET.get("page")
        page_obj = paginator.get_page(page_number)

        if highlight_id is not None:
            if highlight_id not in {task.pk for task in page_obj.object_list}:
                highlight_page = locate_task_definition_page(queryset, highlight_id, paginator.per_page)
                if highlight_page is not None and highlight_page != page_obj.number:
                    page_obj = paginator.get_page(highlight_page)

        task_rows = build_task_definition_rows(page_obj.object_list)
        context["task_definition_rows"] = task_rows
        context["task_definition_page_obj"] = page_obj
        context["task_definition_paginator"] = paginator
        context["task_definition_total_count"] = paginator.count

        if highlight_id is not None:
            context["task_definition_highlight_id"] = highlight_id

        context["task_definition_page_numbers"] = build_compact_page_range(page_obj)

        remaining_params = self.request.GET.copy()
        remaining_params._mutable = True
        if "page" in remaining_params:
            remaining_params.pop("page")
        for key in TASK_FILTER_PARAM_NAMES:
            default_value = getattr(defaults, key)
            current_value = getattr(filters, key)
            if current_value and current_value != default_value:
                remaining_params[key] = current_value
            elif key in remaining_params:
                remaining_params.pop(key)
        if remaining_params.get("group_primary") in {None, "", "none"}:
            remaining_params.pop("group_primary", None)
        if remaining_params.get("group_secondary") in {None, "", "none"}:
            remaining_params.pop("group_secondary", None)
        remaining_params["tm_tab"] = "tareas"
        querystring = remaining_params.urlencode()
        context["task_definition_querystring"] = querystring
        context["task_definition_page_query_prefix"] = f"?{querystring}&" if querystring else "?"

        if paginator.count:
            start_index = (page_obj.number - 1) * paginator.per_page + 1
            end_index = start_index + len(page_obj.object_list) - 1
        else:
            start_index = 0
            end_index = 0
        context["task_definition_page_start"] = start_index
        context["task_definition_page_end"] = end_index

        group_primary_groups = build_grouping_primary_filter_groups()
        default_group_primary = "status"
        raw_group_primary = (self.request.GET.get("group_primary") or "").strip()
        group_primary_candidate = raw_group_primary or default_group_primary
        if resolve_option_label(group_primary_groups, group_primary_candidate) is None:
            group_primary_candidate = default_group_primary
        group_primary_value = group_primary_candidate
        group_primary_label = resolve_option_label(group_primary_groups, group_primary_value) or _("Sin agrupación")

        group_secondary_groups = build_grouping_secondary_filter_groups()
        default_group_secondary = "responsible"
        raw_group_secondary = (self.request.GET.get("group_secondary") or "").strip()
        group_secondary_candidate = raw_group_secondary or default_group_secondary
        if resolve_option_label(group_secondary_groups, group_secondary_candidate) is None:
            group_secondary_candidate = default_group_secondary
        if group_secondary_candidate == group_primary_value and group_secondary_candidate != "none":
            group_secondary_candidate = "none"
        group_secondary_value = group_secondary_candidate
        group_secondary_label = resolve_option_label(group_secondary_groups, group_secondary_value) or _("No aplicar")

        schedule_start_date = _parse_iso_date(filters.scheduled_start)
        schedule_end_date = _parse_iso_date(filters.scheduled_end)
        if schedule_start_date and schedule_end_date and schedule_start_date == schedule_end_date:
            schedule_filter_label = date_format(schedule_start_date, "DATE_FORMAT")
        elif schedule_start_date and schedule_end_date:
            start_label = date_format(schedule_start_date, "DATE_FORMAT")
            end_label = date_format(schedule_end_date, "DATE_FORMAT")
            schedule_filter_label = _("%(start)s – %(end)s") % {"start": start_label, "end": end_label}
        elif schedule_start_date:
            schedule_filter_label = date_format(schedule_start_date, "DATE_FORMAT")
        elif schedule_end_date:
            schedule_filter_label = _("Hasta %(date)s") % {"date": date_format(schedule_end_date, "DATE_FORMAT")}
        else:
            schedule_filter_label = ""

        context["task_manager_status_filter"] = FilterPickerData(
            default_value=status_value,
            default_label=status_label,
            groups=status_groups,
            neutral_value=defaults.status,
        )
        context["task_manager_category_filter"] = FilterPickerData(
            default_value=category_value,
            default_label=category_label,
            groups=category_groups,
            neutral_value=defaults.category,
        )
        context["task_manager_mandatory_filter"] = FilterPickerData(
            default_value=mandatory_value,
            default_label=mandatory_label,
            groups=mandatory_groups,
            neutral_value=defaults.mandatory,
        )
        context["task_manager_criticality_filter"] = FilterPickerData(
            default_value=criticality_value,
            default_label=criticality_label,
            groups=criticality_groups,
            neutral_value=defaults.criticality,
        )
        context["task_manager_scope_filter"] = FilterPickerData(
            default_value=scope_value,
            default_label=scope_label,
            groups=scope_groups,
            search_enabled=True,
            neutral_value=defaults.scope,
        )
        context["task_manager_responsible_filter"] = FilterPickerData(
            default_value=responsible_value,
            default_label=responsible_label,
            groups=responsible_groups,
            search_enabled=True,
            neutral_value=defaults.responsible,
        )
        context["task_manager_schedule_filter"] = {
            "start_value": filters.scheduled_start,
            "end_value": filters.scheduled_end,
        }
        context["task_manager_search_filter"] = (filters.search or "").strip()
        context["task_manager_group_primary_filter"] = FilterPickerData(
            default_value=group_primary_value,
            default_label=group_primary_label,
            groups=group_primary_groups,
            neutral_value="none",
        )
        context["task_manager_group_secondary_filter"] = FilterPickerData(
            default_value=group_secondary_value,
            default_label=group_secondary_label,
            groups=group_secondary_groups,
            neutral_value="none",
        )
        context["task_definition_group_primary"] = group_primary_value
        context["task_definition_group_secondary"] = group_secondary_value

        active_filters: list[ActiveFilterChip] = []
        if filters.status != defaults.status:
            active_filters.append(
                build_active_filter_chip(self.request, "status", _("Estado"), status_label, status_value)
            )
        if filters.category != defaults.category:
            active_filters.append(
                build_active_filter_chip(self.request, "category", _("Categoría"), category_label, category_value)
            )
        if filters.mandatory != defaults.mandatory:
            active_filters.append(
                build_active_filter_chip(
                    self.request,
                    "mandatory",
                    _("Obligatoriedad"),
                    mandatory_label,
                    mandatory_value,
                )
            )
        if filters.criticality != defaults.criticality:
            active_filters.append(
                build_active_filter_chip(
                    self.request,
                    "criticality",
                    _("Nivel de criticidad"),
                    criticality_label,
                    criticality_value,
                )
            )
        if filters.scope != defaults.scope:
            active_filters.append(
                build_active_filter_chip(self.request, "scope", _("Lugar"), scope_label, scope_value)
            )
        if filters.responsible != defaults.responsible:
            active_filters.append(
                build_active_filter_chip(
                    self.request,
                    "responsible",
                    _("Responsable"),
                    responsible_label,
                    responsible_value,
                )
            )
        search_value = (filters.search or "").strip()
        if search_value:
            active_filters.append(
                build_active_filter_chip(
                    self.request,
                    "search",
                    _("Búsqueda"),
                    search_value,
                    search_value,
                )
            )
        if schedule_filter_label:
            active_filters.append(
                build_active_filter_chip(
                    self.request,
                    "scheduled_range",
                    _("Programación"),
                    schedule_filter_label,
                    f"{filters.scheduled_start or ''}:{filters.scheduled_end or ''}",
                    remove_keys=("scheduled_start", "scheduled_end"),
                )
            )

        context["task_definition_active_filters"] = active_filters
        context["task_definition_filters_applied"] = bool(active_filters)
        context["task_definition_clear_filters_url"] = build_clear_filters_url(self.request)
        context["task_manager_assignment_farm_filter"] = FilterPickerData(
            default_value="all",
            default_label=_("Todas las granjas"),
            groups=build_assignment_farm_filter_groups(),
        )
        context["task_manager_assignment_house_filter"] = FilterPickerData(
            default_value="all",
            default_label=_("Todos los galpones"),
            groups=build_assignment_house_filter_groups(),
        )
        context["task_manager_assignment_state_filter"] = FilterPickerData(
            default_value="all",
            default_label=_("Todos los estados"),
            groups=build_assignment_state_filter_groups(),
        )
        context["task_manager_followup_period_filter"] = FilterPickerData(
            default_value="week",
            default_label=_("Semana actual"),
            groups=build_followup_period_filter_groups(),
        )
        context["task_manager_today_view_filter"] = FilterPickerData(
            default_value="priority",
            default_label=_("Prioridad"),
            groups=build_today_view_filter_groups(),
        )
        report_date = _parse_iso_date(self.request.GET.get("tm_report_date")) or timezone.localdate()
        context["task_manager_tab_urls"] = {
            "tareas": build_task_manager_tab_url(
                self.request,
                tab="tareas",
                params={"tm_report_date": None},
            ),
            "reporte": build_task_manager_tab_url(
                self.request,
                tab="reporte",
                params={"tm_report_date": report_date.isoformat()},
            ),
        }
        context["task_manager_daily_report"] = build_daily_assignment_report(
            self.request,
            target_date=report_date,
        )
        return context


task_manager_home_view = TaskManagerHomeView.as_view()


def _build_telegram_mini_app_payload(
    *,
    date_label: str,
    display_name: str,
    contact_handle: str,
    role: str,
    initials: str,
    shift_confirmation: Optional[dict[str, object]] = None,
    shift_confirmation_empty: Optional[dict[str, object]] = None,
    include_shift_confirmation_stub: bool = True,
    user: Optional[UserProfile] = None,
    production: Optional[dict[str, object]] = None,
    feed_plan: Optional[dict[str, object]] = None,
    night_mortality: Optional[dict[str, object]] = None,
    weight_registry: Optional[dict[str, object]] = None,
    include_weight_registry: bool = True,
    purchases: Optional[dict[str, object]] = None,
) -> dict[str, object]:
    today = timezone.localdate()
    weekday_label = date_format(today, "l").capitalize()
    day_number = date_format(today, "d")
    month_label = date_format(today, "M").strip(".").lower()
    tomorrow = today + timedelta(days=1)
    day_minus_1 = today - timedelta(days=1)
    day_minus_2 = today - timedelta(days=2)
    day_minus_3 = today - timedelta(days=3)
    day_plus_2 = today + timedelta(days=2)

    transport_stage = build_transport_stage_payload(user=user)
    verification_stage = build_transport_verification_payload()
    transport_manifest_entries = transport_stage.get("manifest", {}).get("entries", []) if transport_stage else []
    transport_manifest_total_cartons = (
        transport_stage.get("manifest", {}).get("total_cartons") if transport_stage else Decimal("0")
    )
    transport_manifest_origin = transport_stage.get("route", {}).get("origin") if transport_stage else None

    cartons_per_pack = 30
    daily_cartons = transport_manifest_total_cartons
    daily_eggs = daily_cartons * cartons_per_pack
    inspection_reference_entry = transport_manifest_entries[0] if transport_manifest_entries else None
    inspection_production_date_label = (
        inspection_reference_entry["production_date_label"]
        if inspection_reference_entry
        else date_format(today, "DATE_FORMAT")
    )
    inspection_farm = inspection_reference_entry["farm"] if inspection_reference_entry else _("Sin asignar")
    inspection_barn = (
        " · ".join(inspection_reference_entry.get("houses", []))
        if inspection_reference_entry and inspection_reference_entry.get("houses")
        else _("Galpón no definido")
    )
    inspection_initial_cartons = daily_cartons
    inspection_transported_cartons = max(daily_cartons - 12, 0)
    inspection_classified_cartons = max(daily_cartons - 18, 0)
    inspection_discarded_cartons = max(inspection_initial_cartons - inspection_classified_cartons, 0)
    inspection_classified_breakdown = [
        {"id": "jumbo", "label": "Jumbo", "cartons": 118, "theme": "emerald"},
        {"id": "aaa", "label": "AAA", "cartons": 210, "theme": "amber"},
        {"id": "aa", "label": "AA", "cartons": 172, "theme": "sky"},
        {"id": "a", "label": "A", "cartons": 140, "theme": "slate"},
        {"id": "b", "label": "B", "cartons": 90, "theme": "violet"},
        {"id": "c", "label": "C", "cartons": 72, "theme": "brand"},
    ]
    inspection_input_categories = [
        {**item, "kind": "sellable"} for item in inspection_classified_breakdown
    ]
    inspection_input_categories.append(
        {"id": "discard", "label": _("Descartados"), "cartons": inspection_discarded_cartons, "theme": "rose", "kind": "discard"}
    )
    inspection_metadata_label = _("%(date)s · %(farm)s%(barn)s") % {
        "date": inspection_production_date_label,
        "farm": inspection_farm,
        "barn": f" · {inspection_barn}" if inspection_barn else "",
    }

    carton_price_map: dict[str, int] = {
        "jumbo": 12500,
        "aaa": 11000,
        "aa": 10500,
        "a": 9800,
        "b": 8800,
        "c": 8200,
    }

    def format_currency(amount: int) -> str:
        return f"$ {intcomma(amount).replace(',', '.')}"

    inventory_reservations = [
        {
            "id": "order-2024-11-05-a",
            "vendor": "Distribuidora La Plaza",
            "channel": _("Mayorista"),
            "contact": "+57 320 555 8899",
            "delivery_date_iso": tomorrow.isoformat(),
            "delivery_date_label": date_format(tomorrow, "DATE_FORMAT"),
            "status": _("Confirmado"),
            "items": [
                {"type_id": "aaa", "label": "AAA", "cartons": 90},
                {"type_id": "aa", "label": "AA", "cartons": 60},
                {"type_id": "a", "label": "A", "cartons": 45},
            ],
        },
        {
            "id": "order-2024-11-06-b",
            "vendor": "Mercados del Norte",
            "channel": _("Retail"),
            "contact": "+57 301 442 7811",
            "delivery_date_iso": day_plus_2.isoformat(),
            "delivery_date_label": date_format(day_plus_2, "DATE_FORMAT"),
            "status": _("Por confirmar"),
            "items": [
                {"type_id": "jumbo", "label": "Jumbo", "cartons": 40},
                {"type_id": "aaa", "label": "AAA", "cartons": 35},
                {"type_id": "b", "label": "B", "cartons": 20},
            ],
        },
    ]

    inventory_reservation_totals: dict[str, int] = {}
    for reservation in inventory_reservations:
        reservation_cartons = 0
        reservation_amount = 0
        for item in reservation["items"]:
            cartons = item["cartons"]
            item["eggs"] = cartons * cartons_per_pack
            carton_price = carton_price_map.get(item["type_id"], 10000)
            item["carton_price"] = carton_price
            item["carton_price_label"] = format_currency(carton_price)
            item["total_amount"] = carton_price * cartons
            item["total_amount_label"] = format_currency(item["total_amount"])
            reservation_cartons += cartons
            reservation_amount += item["total_amount"]
            inventory_reservation_totals[item["type_id"]] = inventory_reservation_totals.get(item["type_id"], 0) + cartons
        reservation["total_cartons"] = reservation_cartons
        reservation["total_eggs"] = reservation_cartons * cartons_per_pack
        reservation["total_amount"] = reservation_amount
        reservation["total_amount_label"] = format_currency(reservation_amount)

    inventory_ready_breakdown = []
    inventory_alerts = []
    for category in inspection_classified_breakdown:
        type_id = category["id"]
        total_cartons = category["cartons"]
        reserved_cartons = inventory_reservation_totals.get(type_id, 0)
        available_cartons = max(total_cartons - reserved_cartons, 0)
        reserved_ratio = round((reserved_cartons / total_cartons) * 100, 1) if total_cartons else 0.0
        breakdown = {
            "id": type_id,
            "label": category["label"],
            "cartons": total_cartons,
            "reserved_cartons": reserved_cartons,
            "available_cartons": available_cartons,
            "eggs": total_cartons * cartons_per_pack,
            "reserved_eggs": reserved_cartons * cartons_per_pack,
            "available_eggs": available_cartons * cartons_per_pack,
            "reserved_ratio": reserved_ratio,
        }
        if available_cartons <= max(round(total_cartons * 0.2), 10):
            inventory_alerts.append(
                _("Tipo %(label)s: solo %(available)s cartones disponibles para venta inmediata.")
                % {"label": category["label"], "available": available_cartons}
            )
        inventory_ready_breakdown.append(breakdown)

    inventory_ready_total_cartons = sum(item["cartons"] for item in inventory_ready_breakdown)
    inventory_reserved_cartons = sum(item["reserved_cartons"] for item in inventory_ready_breakdown)
    inventory_available_cartons = sum(item["available_cartons"] for item in inventory_ready_breakdown)

    inventory_ready_summary = {
        "total_cartons": inventory_ready_total_cartons,
        "reserved_cartons": inventory_reserved_cartons,
        "available_cartons": inventory_available_cartons,
        "total_eggs": inventory_ready_total_cartons * cartons_per_pack,
        "available_eggs": inventory_available_cartons * cartons_per_pack,
        "breakdown": inventory_ready_breakdown,
        "reservations": inventory_reservations,
        "alerts": inventory_alerts,
    }

    dispatch_type_catalog = [
        {"id": "jumbo", "label": "Jumbo", "unit": _("cartones"), "unit_key": "cartons"},
        {"id": "aaa", "label": "AAA", "unit": _("cartones"), "unit_key": "cartons"},
        {"id": "aa", "label": "AA", "unit": _("cartones"), "unit_key": "cartons"},
        {"id": "a", "label": "A", "unit": _("cartones"), "unit_key": "cartons"},
        {"id": "b", "label": "B", "unit": _("cartones"), "unit_key": "cartons"},
        {"id": "c", "label": "C", "unit": _("cartones"), "unit_key": "cartons"},
        {"id": "live_hens", "label": _("Gallinas vivas"), "unit": _("aves"), "unit_key": "hens"},
        {"id": "processed_hens", "label": _("Gallinas faenadas"), "unit": _("aves"), "unit_key": "hens"},
    ]

    dispatch_vendor_options = [
        {"id": "vendor-plaza", "name": "Distribuidora La Plaza", "channel": _("Mayorista")},
        {"id": "vendor-norte", "name": "Mercados del Norte", "channel": _("Retail")},
        {"id": "vendor-fresco", "name": "Huevos Frescos del Centro", "channel": _("Mayorista")},
    ]

    dispatch_driver_options = [
        {"id": "driver-carlos", "name": "Carlos Pérez", "phone": "+57 320 555 2211"},
        {"id": "driver-andrea", "name": "Andrea Ríos", "phone": "+57 301 884 7733"},
        {"id": "driver-luis", "name": "Luis Martínez", "phone": "+57 314 992 1150"},
    ]

    dispatch_vehicle_options = [
        {"id": "vehicle-npr-01", "label": _("Camión NPR refrigerado"), "plate": "TES-123"},
        {"id": "vehicle-npr-02", "label": _("Camión NPR seco"), "plate": "GDF-908"},
        {"id": "vehicle-van-01", "label": _("Van refrigerada"), "plate": "XTY-445"},
    ]

    dispatch_status_palette = {
        "planned": {"label": _("Programado"), "theme": "slate"},
        "loading": {"label": _("Cargando"), "theme": "brand"},
        "issue": {"label": _("Imprevisto"), "theme": "rose"},
        "completed": {"label": _("Despachado"), "theme": "emerald"},
    }

    dispatch_status_options = [
        {"id": status_key, "label": status_data["label"], "theme": status_data["theme"]}
        for status_key, status_data in dispatch_status_palette.items()
    ]

    dispatches: list[dict[str, Any]] = [
        {
            "id": "dispatch-dlp-2024-11-05",
            "code": "DSP-241105-A",
            "vendor": dispatch_vendor_options[0],
            "seller": "Laura Gómez",
            "scheduled_date_iso": tomorrow.isoformat(),
            "scheduled_date_label": date_format(tomorrow, "DATE_FORMAT"),
            "status": "loading",
            "driver": dispatch_driver_options[0],
            "vehicle": dispatch_vehicle_options[0],
            "contact": "+57 315 774 0021",
            "notes": _("Verificar temperatura de la cava antes de salir."),
            "items": [
                {"type_id": "aaa", "label": "AAA", "unit": "cartons", "requested": 90, "confirmed": 90},
                {"type_id": "aa", "label": "AA", "unit": "cartons", "requested": 60, "confirmed": 58},
                {"type_id": "processed_hens", "label": _("Gallinas faenadas"), "unit": "hens", "requested": 120, "confirmed": 118},
            ],
        },
        {
            "id": "dispatch-mn-2024-11-06",
            "code": "DSP-241106-B",
            "vendor": dispatch_vendor_options[1],
            "seller": "Juan Rodríguez",
            "scheduled_date_iso": day_plus_2.isoformat(),
            "scheduled_date_label": date_format(day_plus_2, "DATE_FORMAT"),
            "status": "planned",
            "driver": dispatch_driver_options[1],
            "vehicle": dispatch_vehicle_options[2],
            "contact": "+57 321 660 4412",
            "notes": _("Coordinar entrega segmentada: primero retail, luego grandes superficies."),
            "items": [
                {"type_id": "jumbo", "label": "Jumbo", "unit": "cartons", "requested": 40, "confirmed": 0},
                {"type_id": "aaa", "label": "AAA", "unit": "cartons", "requested": 32, "confirmed": 0},
                {"type_id": "live_hens", "label": _("Gallinas vivas"), "unit": "hens", "requested": 80, "confirmed": 0},
            ],
        },
        {
            "id": "dispatch-hfc-2024-11-04",
            "code": "DSP-241104-C",
            "vendor": dispatch_vendor_options[2],
            "seller": "María Fernanda Torres",
            "scheduled_date_iso": today.isoformat(),
            "scheduled_date_label": date_format(today, "DATE_FORMAT"),
            "status": "issue",
            "driver": dispatch_driver_options[2],
            "vehicle": dispatch_vehicle_options[1],
            "contact": "+57 312 223 1199",
            "notes": _("Confirmar recepción con turno de la tarde."),
            "items": [
                {"type_id": "aa", "label": "AA", "unit": "cartons", "requested": 48, "confirmed": 48},
                {"type_id": "a", "label": "A", "unit": "cartons", "requested": 36, "confirmed": 36},
                {"type_id": "b", "label": "B", "unit": "cartons", "requested": 24, "confirmed": 24},
                {"type_id": "live_hens", "label": _("Gallinas vivas"), "unit": "hens", "requested": 60, "confirmed": 60},
            ],
        },
    ]

    total_confirmed_cartons = 0
    total_confirmed_hens = 0
    pending_cartons = 0
    pending_hens = 0
    unique_vendor_ids: set[str] = set()

    for dispatch in dispatches:
        vendor = dispatch["vendor"]
        unique_vendor_ids.add(vendor["id"])
        status_key = dispatch["status"]
        status_palette = dispatch_status_palette.get(status_key, dispatch_status_palette["planned"])
        dispatch["status_label"] = status_palette["label"]
        dispatch["status_theme"] = status_palette["theme"]

        requested_cartons = 0
        confirmed_cartons = 0
        requested_hens = 0
        confirmed_hens = 0
        item_map: dict[str, dict[str, Any]] = {}

        for item in dispatch["items"]:
            unit = item["unit"]
            requested = item["requested"]
            confirmed = item["confirmed"]
            difference = max(requested - confirmed, 0)
            item["difference"] = difference
            item_map[item["type_id"]] = item
            if unit == "cartons":
                item["unit_label"] = _("cartones")
                requested_cartons += requested
                confirmed_cartons += confirmed
                pending_cartons += difference
            else:
                item["unit_label"] = _("aves")
                requested_hens += requested
                confirmed_hens += confirmed
                pending_hens += difference

        dispatch["totals"] = {
            "requested_cartons": requested_cartons,
            "confirmed_cartons": confirmed_cartons,
            "requested_hens": requested_hens,
            "confirmed_hens": confirmed_hens,
        }
        catalog_rows: list[dict[str, Any]] = []
        for catalog_entry in dispatch_type_catalog:
            type_id = catalog_entry["id"]
            catalog_unit = catalog_entry["unit_key"]
            item = item_map.get(type_id)
            requested_value = item["requested"] if item else 0
            confirmed_value = item["confirmed"] if item else 0
            difference_value = item["difference"] if item else 0
            catalog_rows.append(
                {
                    "type_id": type_id,
                    "label": catalog_entry["label"],
                    "unit_label": catalog_entry["unit"],
                    "unit": catalog_unit,
                    "requested": requested_value,
                    "confirmed": confirmed_value,
                    "difference": difference_value,
                }
            )
        dispatch["catalog_rows"] = catalog_rows
        dispatch["item_map"] = item_map
        total_confirmed_cartons += confirmed_cartons
        total_confirmed_hens += confirmed_hens

    dispatch_stage_metrics = [
        {"label": _("Despachos programados"), "value": len(dispatches), "unit": _("rutas")},
        {"label": _("Cartones confirmados"), "value": total_confirmed_cartons, "unit": _("cartones")},
        {"label": _("Aves confirmadas"), "value": total_confirmed_hens, "unit": _("aves")},
    ]

    pending_badges = []
    if pending_cartons:
        pending_badges.append(_("Faltan %(value)s cartones por confirmar") % {"value": intcomma(pending_cartons)})
    if pending_hens:
        pending_badges.append(_("Faltan %(value)s aves por confirmar") % {"value": intcomma(pending_hens)})

    dispatch_form_defaults = {
        "scheduled_date_label": date_format(tomorrow, "DATE_FORMAT"),
        "scheduled_date_value": tomorrow.isoformat(),
        "drivers": dispatch_driver_options,
        "vendors": dispatch_vendor_options,
        "vehicles": dispatch_vehicle_options,
        "type_catalog": dispatch_type_catalog,
        "cartons_per_pack": cartons_per_pack,
    }

    dispatch_summary = {
        "metrics": dispatch_stage_metrics,
        "vendors_count": len(unique_vendor_ids),
        "pending_badges": pending_badges,
        "form": dispatch_form_defaults,
        "dispatches": dispatches,
        "status_options": dispatch_status_options,
    }

    classification_categories = [
        {"id": "jumbo", "label": "Jumbo", "cartons": 85, "theme": "emerald"},
        {"id": "aaa", "label": "AAA", "cartons": 210, "theme": "amber"},
        {"id": "aa", "label": "AA", "cartons": 180, "theme": "sky"},
        {"id": "a", "label": "A", "cartons": 150, "theme": "slate"},
        {"id": "b", "label": "B", "cartons": 110, "theme": "violet"},
        {"id": "c", "label": "C", "cartons": 60, "theme": "brand"},
        {"id": "discard", "label": "D (Descarte)", "cartons": 25, "theme": "rose"},
    ]

    for category in classification_categories:
        cartons = category["cartons"]
        eggs = cartons * cartons_per_pack
        percentage = round((eggs / daily_eggs) * 100, 1) if daily_eggs else 0.0
        category["eggs"] = eggs
        category["percentage"] = percentage

    production_reference = {
        "active_hens": production.get("active_hens", 0) if production else 0,
        "label": _("Aves en postura activas"),
    }

    weight_registry_payload = weight_registry if include_weight_registry else None

    transport_stage["progress_url"] = reverse("task_manager:mini-app-transport-progress")
    transport_stage["confirmation_url"] = reverse("task_manager:mini-app-transport-confirmation")
    verification_stage["submit_url"] = reverse("task_manager:mini-app-transport-verification")

    workflow_stages: list[dict[str, object]] = [transport_stage, verification_stage]

    egg_workflow = {
        "cartons_per_pack": cartons_per_pack,
        "batch": {
            "label": _("Manifiesto interno · semana %(week)s") % {"week": date_format(today, "W")},
            "origin": _("Múltiples granjas · ver detalle en transporte"),
            "rooms": ["Galpón 3", "Galpón 4", "Galpón 5", "Galpón 1"],
            "produced_cartons": daily_cartons,
            "produced_eggs": daily_eggs,
            "recorded_at": date_format(today, "d M Y"),
        },
        "stages": workflow_stages + [
            {
                "id": "classification",
                "icon": "🥚",
                "title": "Clasificación por tipos",
                "tone": "emerald",
                "status": "pending",
                "summary": "Distribuye los huevos por tipo y conserva la equivalencia con el lote recibido.",
                "metrics": [
                    {"label": "Cartones a clasificar", "value": daily_cartons, "unit": "cartones"},
                    {"label": "Huevos", "value": daily_eggs, "unit": "huevos"},
                ],
                "categories": classification_categories,
            },
            {
                "id": "inspection",
                "icon": "🔍",
                "title": "Inspección final",
                "tone": "slate",
                "status": "pending",
                "summary": _(
                    "Valida el cierre del lote y confirma cuántos cartones quedan listos para la venta después de la inspección."
                ),
                "overview": {
                    "production_date_label": inspection_production_date_label,
                    "farm": inspection_farm,
                    "barn": inspection_barn,
                    "metadata_label": inspection_metadata_label,
                    "initial_cartons": inspection_initial_cartons,
                    "transported_cartons": inspection_transported_cartons,
                    "classified_cartons": inspection_classified_cartons,
                    "discarded_cartons": inspection_discarded_cartons,
                    "classified_breakdown": inspection_classified_breakdown,
                },
                "inspection_categories": inspection_input_categories,
                "fields": [
                    {
                        "id": "notes",
                        "label": "Observaciones",
                        "placeholder": _("Ej: Ajustar temperatura de cámara antes de liberar"),
                        "multiline": True,
                    },
                ],
                "checkpoints": [
                    _("Confirma limpieza del área y registra fotos si hubo descartes."),
                    _("Comunica ajustes de bioseguridad al supervisor antes del cierre."),
                ],
            },
            {
                "id": "inventory_ready",
                "icon": "🏷️",
                "title": _("Inventario listo para venta"),
                "tone": "emerald",
                "status": "monitoring",
                "summary": None,
                "metrics": [
                    {"label": _("Total clasificado"), "value": inventory_ready_summary["total_cartons"], "unit": _("cartones")},
                    {"label": _("Reservado"), "value": inventory_ready_summary["reserved_cartons"], "unit": _("cartones")},
                    {"label": _("Disponible"), "value": inventory_ready_summary["available_cartons"], "unit": _("cartones")},
                ],
                "inventory": inventory_ready_summary,
                "checkpoints": [
                    _("Cruza el inventario físico con las reservas antes de comprometer nuevos pedidos."),
                ],
            },
            {
                "id": "dispatches",
                "icon": "🗂️",
                "title": _("Despachos a ventas"),
                "tone": "slate",
                "status": "planning",
                "summary": _(
                    "Concentra los envíos programados y confirma cantidades finales por canal antes de liberar al transporte."
                ),
                "metrics": dispatch_summary["metrics"],
                "dispatches": dispatch_summary["dispatches"],
                "vendors_count": dispatch_summary["vendors_count"],
                "pending_badges": dispatch_summary["pending_badges"],
                "form": dispatch_summary["form"],
                "type_catalog": dispatch_type_catalog,
            },
        ],
        "dispatch_summary": dispatch_summary,
    }

    transport_queue = build_transport_queue_payload()
    transport_queue["submit_url"] = reverse("task_manager:mini-app-transport-authorize")

    pending_classification_sources = [
        {
            "id": "gs-g3-2024-10-31",
            "farm": "Granja San Lucas",
            "barn": "Galpón 3",
            "cartons": 210,
            "production_date": day_minus_3,
            "status": "pending",
        },
        {
            "id": "pr-g5-2024-11-01",
            "farm": "Granja Providencia",
            "barn": "Galpón 5",
            "cartons": 185,
            "production_date": day_minus_2,
            "status": "in_progress",
            "responsible": "Equipo nocturno",
        },
        {
            "id": "lp-g1-2024-11-02",
            "farm": "Granja La Primavera",
            "barn": "Galpón 1",
            "cartons": 196,
            "production_date": day_minus_1,
            "status": "pending",
        },
        {
            "id": "lc-g2-2024-11-03",
            "farm": "Granja La Colina",
            "barn": "Galpón 2",
            "cartons": 172,
            "production_date": today,
            "status": "pending",
        },
    ]

    pending_status_meta = {
        "pending": {"label": _("Pendiente"), "theme": "rose"},
        "in_progress": {"label": _("En clasificación"), "theme": "brand"},
    }

    pending_classification_entries = []
    pending_classification_alerts = []
    pending_status_counts: dict[str, int] = {}
    for source in pending_classification_sources:
        production_date = source["production_date"]
        age_days = max((today - production_date).days, 0)
        if age_days == 1:
            age_label = _("%(days)s día en espera") % {"days": age_days}
        else:
            age_label = _("%(days)s días en espera") % {"days": age_days}

        status_key = source["status"]
        status_meta = pending_status_meta.get(status_key, {"label": status_key.title(), "theme": "slate"})
        alerts = []
        if age_days >= 3:
            alerts.append(
                _("Prioritario: %(farm)s · %(barn)s lleva %(days)s días sin clasificar.")
                % {"farm": source["farm"], "barn": source["barn"], "days": age_days}
            )
        entry = {
            "id": source["id"],
            "farm": source["farm"],
            "barn": source["barn"],
            "cartons": source["cartons"],
            "eggs": source["cartons"] * cartons_per_pack,
            "production_date_iso": production_date.isoformat(),
            "production_date_label": date_format(production_date, "DATE_FORMAT"),
            "age_days": age_days,
            "age_label": age_label,
            "status": status_key,
            "status_label": status_meta["label"],
            "status_theme": status_meta["theme"],
            "alerts": alerts,
            "is_priority": age_days >= 3,
        }
        if source.get("responsible"):
            entry["responsible"] = source["responsible"]
        pending_classification_entries.append(entry)
        pending_status_counts[status_key] = pending_status_counts.get(status_key, 0) + 1
        pending_classification_alerts.extend(alerts)

    pending_classification_total_cartons = sum(entry["cartons"] for entry in pending_classification_entries)
    pending_classification_total_eggs = pending_classification_total_cartons * cartons_per_pack

    if pending_classification_entries:
        oldest_age = max(entry["age_days"] for entry in pending_classification_entries)
        if oldest_age >= 2:
            oldest_locations = [
                _("%(farm)s · %(barn)s") % {"farm": entry["farm"], "barn": entry["barn"]}
                for entry in pending_classification_entries
                if entry["age_days"] == oldest_age
            ]
            pending_classification_alerts.append(
                _("Atiende primero: %(locations)s (%(days)s días en espera).")
                % {"locations": ", ".join(oldest_locations), "days": oldest_age}
            )

    pending_classification_summary = {
        "title": _("Producciones pendientes por clasificar"),
        "description": _(
            ""
        ),
        "total_cartons": pending_classification_total_cartons,
        "total_eggs": pending_classification_total_eggs,
        "entries": pending_classification_entries,
        "alerts": pending_classification_alerts,
        "status_counts": [
            {"id": "pending", "label": _("Pendiente"), "count": pending_status_counts.get("pending", 0)},
            {"id": "in_progress", "label": _("En clasificación"), "count": pending_status_counts.get("in_progress", 0)},
        ],
    }

    shift_confirmation_payload = shift_confirmation
    shift_confirmation_empty_payload = shift_confirmation_empty

    if shift_confirmation_payload is None and shift_confirmation_empty_payload is None and include_shift_confirmation_stub:
        default_summary = "Operaciones - Bioseguridad · Auxiliar operativo"
        shift_confirmation_payload = {
            "assignment_id": None,
            "calendar_id": None,
            "date": today.isoformat(),
            "greeting_label": _("Hola %(name)s, es %(weekday)s %(day)s de %(month)s")
            % {"name": display_name, "weekday": weekday_label, "day": day_number, "month": month_label},
            "date_label": _("Hoy, %(weekday)s %(day)s de %(month)s")
            % {"weekday": weekday_label, "day": day_number, "month": month_label},
            "summary_label": default_summary,
            "category_label": "Operaciones - Bioseguridad",
            "position_label": "Auxiliar operativo",
            "farm": "Granja La Colina",
            "barn": "Galpón 3",
            "rooms": ["Sala 1", "Sala 2"],
            "handoff_to": "Lucía Pérez",
            "requires_confirmation": True,
            "confirmed": False,
            "storage_key": f"miniapp-shift-confirm::{today.isoformat()}",
            "shift_type": ShiftType.NIGHT,
        }

    tasks = _resolve_daily_task_cards(user=user, reference_date=today)

    daily_assignment_schedule = _resolve_operator_daily_assignments(
        user=user,
        reference_date=today,
        max_days=6,
    )

    leader_review_days = [
        {
            "date_iso": day_minus_3.isoformat(),
            "date_label": date_format(day_minus_3, "DATE_FORMAT"),
            "weekday_label": date_format(day_minus_3, "l").capitalize(),
            "shift_windows": [_("Turno diurno · 05:00 – 13:00")],
            "locations": [
                {
                    "id": "lp-g1-s2",
                    "label": _("Granja La Primavera · Galpón 1 · Sala 2"),
                    "farm": "Granja La Primavera",
                    "barn": "Galpón 1",
                    "room": "Sala 2",
                    "shift_label": _("Turno diurno"),
                    "tasks": [
                        {
                            "id": "review-lp-g1-s2-001",
                            "title": _("Limpieza profunda de nidos"),
                            "priority": {"label": _("Alta"), "theme": "critical"},
                            "responsible": "Sandra Leal",
                            "status": {
                                "state": "completed",
                                "label": _("Completada"),
                                "details": _("Marcada 02 Nov · 19:40"),
                            },
                            "review": {
                                "state": "awaiting",
                                "label": _("En revisión"),
                                "details": _("Evidencia subida · espera aprobación"),
                            },
                            "description": _(
                                "Asegurar desinfección completa y retiro de material orgánico en nidos de la línea B."
                            ),
                            "evidence_count": 3,
                            "duration_label": _("85 minutos"),
                            "execution_window": _("02 Nov · 17:00 – 19:30"),
                            "tags": [_("Bioseguridad"), _("Correctivo")],
                            "recommendation_placeholder": _("Agrega una recomendación para próximas jornadas"),
                        },
                        {
                            "id": "review-lp-g1-s2-002",
                            "title": _("Verificación de sensores ambiente"),
                            "priority": {"label": _("Media"), "theme": "brand"},
                            "responsible": "Kevin Duarte",
                            "status": {
                                "state": "overdue",
                                "label": _("Vencida"),
                                "details": _("Sin reporte"),
                                "overdue_days": 2,
                            },
                            "review": {
                                "state": "missing",
                                "label": _("Sin evidencia"),
                                "details": _("Recuerda solicitar soporte al final del turno."),
                            },
                            "description": _("Calibrar sensores de CO₂ y humedad. Registrar lecturas en la app."),
                            "evidence_count": 0,
                            "execution_window": _("02 Nov · 11:00 – 12:00"),
                            "tags": [_("Monitoreo"), _("Turno diurno")],
                            "alerts": [_("Priorizar calibración antes del siguiente lote.")],
                            "recommendation_placeholder": _("Usa este espacio para anotar ajustes o recordatorios"),
                        },
                    ],
                }
            ],
        },
        {
            "date_iso": day_minus_2.isoformat(),
            "date_label": date_format(day_minus_2, "DATE_FORMAT"),
            "weekday_label": date_format(day_minus_2, "l").capitalize(),
            "shift_windows": [_("Turno nocturno · 22:00 – 06:00")],
            "locations": [
                {
                    "id": "pr-g5-s3",
                    "label": _("Granja Providencia · Galpón 5 · Sala 3"),
                    "farm": "Granja Providencia",
                    "barn": "Galpón 5",
                    "room": "Sala 3",
                    "shift_label": _("Turno nocturno"),
                    "tasks": [
                        {
                            "id": "review-pr-g5-s3-001",
                            "title": _("Revisión de válvulas de bebederos"),
                            "priority": {"label": _("Alta"), "theme": "critical"},
                            "responsible": "Ana Torres",
                            "status": {
                                "state": "completed",
                                "label": _("Completada"),
                                "details": _("Marcada 03 Nov · 05:10"),
                            },
                            "review": {
                                "state": "awaiting",
                                "label": _("En revisión"),
                                "details": _("Evidencia lista para validar"),
                            },
                            "description": _(
                                "Validar presión y fugas en la línea secundaria. Adjuntar comprobantes fotográficos."
                            ),
                            "evidence_count": 4,
                            "execution_window": _("03 Nov · 01:30 – 02:15"),
                            "tags": [_("Mantenimiento"), _("Agua")],
                            "recommendation_placeholder": _("Sugiere mejoras o seguimiento puntual"),
                        },
                        {
                            "id": "review-pr-g5-s3-002",
                            "title": _("Control de inventario de vacunas"),
                            "priority": {"label": _("Alta"), "theme": "brand"},
                            "responsible": "Miguel Ríos",
                            "status": {
                                "state": "completed",
                                "label": _("Completada"),
                                "details": _("Marcada 03 Nov · 02:40"),
                            },
                            "review": {
                                "state": "approved",
                                "label": _("Aprobada"),
                                "details": _("Revisión completada 03 Nov · 06:15"),
                            },
                            "description": _("Verificar stock disponible y registrar lotes abiertos."),
                            "evidence_count": 2,
                            "execution_window": _("03 Nov · 00:30 – 01:15"),
                            "tags": [_("Inventario"), _("Vacunas")],
                            "recommendation_placeholder": _("Añade notas para la próxima auditoría"),
                        },
                    ],
                }
            ],
        },
        {
            "date_iso": day_minus_1.isoformat(),
            "date_label": date_format(day_minus_1, "DATE_FORMAT"),
            "weekday_label": date_format(day_minus_1, "l").capitalize(),
            "shift_windows": [_("Turno nocturno · 22:00 – 06:00")],
            "locations": [
                {
                    "id": "sl-g4-s1",
                    "label": _("Granja San Lucas · Galpón 4 · Sala 1"),
                    "farm": "Granja San Lucas",
                    "barn": "Galpón 4",
                    "room": "Sala 1",
                    "shift_label": _("Turno nocturno"),
                    "tasks": [
                        {
                            "id": "review-sl-g4-s1-001",
                            "title": _("Control de ventilación nocturna"),
                            "priority": {"label": _("Media"), "theme": "brand"},
                            "responsible": "Lucía Hernández",
                            "status": {
                                "state": "completed",
                                "label": _("Completada"),
                                "details": _("Marcada 04 Nov · 05:55"),
                            },
                            "review": {
                                "state": "awaiting",
                                "label": _("En revisión"),
                                "details": _("Incluye mediciones cada hora."),
                            },
                            "description": _(
                                "Registrar apertura de compuertas y valores de CO₂ por tramo. Adjuntar lectura inicial y final."
                            ),
                            "evidence_count": 5,
                            "execution_window": _("04 Nov · 22:15 – 05:30"),
                            "tags": [_("Ambiente"), _("CO₂")],
                            "recommendation_placeholder": _("Comparte hallazgos para el turno diurno"),
                        },
                        {
                            "id": "review-sl-g4-s1-002",
                            "title": _("Recorridos bioseguridad galpón"),
                            "priority": {"label": _("Media"), "theme": "success"},
                            "responsible": "Jairo Téllez",
                            "status": {
                                "state": "pending",
                                "label": _("Pendiente"),
                                "details": _("Reportado sin evidencia"),
                            },
                            "review": {
                                "state": "missing",
                                "label": _("Falta evidencia"),
                                "details": _("Solicita fotos antes de aprobar."),
                            },
                            "description": _(
                                "Verificar pediluvios, cambio de botas y registros de visitantes. Adjuntar fotos por estación."
                            ),
                            "evidence_count": 0,
                            "execution_window": _("04 Nov · 23:00 – 23:45"),
                            "tags": [_("Bioseguridad"), _("Recorridos")],
                            "recommendation_placeholder": _("Anota qué esperas ver en la siguiente revisión"),
                        },
                        {
                            "id": "review-sl-g4-s1-003",
                            "title": _("Reporte de novedades de postura"),
                            "priority": {"label": _("Alta"), "theme": "critical"},
                            "responsible": "Camilo Ortiz",
                            "status": {
                                "state": "overdue",
                                "label": _("Vencida"),
                                "details": _("Último registro 02 Nov"),
                                "overdue_days": 1,
                            },
                            "review": {
                                "state": "missing",
                                "label": _("Sin evidencia"),
                                "details": _("Recuerda cargar el formato antes de aprobar."),
                            },
                            "description": _("Registrar variaciones de postura superiores al 3 %."),
                            "evidence_count": 0,
                            "execution_window": _("04 Nov · 02:30 – 03:00"),
                            "tags": [_("Producción"), _("Seguimiento")],
                            "recommendation_placeholder": _("Comparte el mensaje que le enviarás al equipo"),
                        },
                    ],
                }
            ],
        },
        {
            "date_iso": today.isoformat(),
            "date_label": date_format(today, "DATE_FORMAT"),
            "weekday_label": date_format(today, "l").capitalize(),
            "shift_windows": [_("Turno nocturno · 22:00 – 06:00"), _("Turno diurno · 06:00 – 14:00")],
            "locations": [
                {
                    "id": "lc-g3-s1",
                    "label": _("Granja La Colina · Galpón 3 · Sala 1"),
                    "farm": "Granja La Colina",
                    "barn": "Galpón 3",
                    "room": "Sala 1",
                    "shift_label": _("Turno nocturno"),
                    "tasks": [
                        {
                            "id": "review-lc-g3-s1-001",
                            "title": _("Cierre sanitario de línea de producción"),
                            "priority": {"label": _("Alta"), "theme": "critical"},
                            "responsible": "Diana Rojas",
                            "status": {
                                "state": "completed",
                                "label": _("Completada"),
                                "details": _("Marcada 05 Nov · 05:20"),
                            },
                            "review": {
                                "state": "awaiting",
                                "label": _("En revisión"),
                                "details": _("Incluye 2 evidencias de apoyo"),
                            },
                            "description": _(
                                "Confirmar cierre de línea con desinfección por nebulización. Adjuntar video corto y checklist."
                            ),
                            "evidence_count": 2,
                            "execution_window": _("05 Nov · 03:40 – 04:55"),
                            "tags": [_("Cierre de turno"), _("Bioseguridad")],
                            "recommendation_placeholder": _("Anota recomendaciones para el relevo"),
                        },
                        {
                            "id": "review-lc-g3-s1-002",
                            "title": _("Balance de mortalidad y descartes"),
                            "priority": {"label": _("Media"), "theme": "brand"},
                            "responsible": "Luis Medina",
                            "status": {
                                "state": "completed",
                                "label": _("Completada"),
                                "details": _("Marcada 05 Nov · 04:35"),
                            },
                            "review": {
                                "state": "awaiting",
                                "label": _("En revisión"),
                                "details": _("Revisa las cifras antes de aprobar."),
                            },
                            "description": _(
                                "Consolidar cifras de mortalidad, descartes y causas. Asegurar conciliación con registros manuales."
                            ),
                            "evidence_count": 1,
                            "execution_window": _("05 Nov · 02:50 – 03:30"),
                            "tags": [_("Producción"), _("Control")],
                            "recommendation_placeholder": _("Deja una nota si hay desvíos"),
                        },
                    ],
                },
                {
                    "id": "lc-g2-salones",
                    "label": _("Granja La Colina · Galpón 2 · Salones comunes"),
                    "farm": "Granja La Colina",
                    "barn": "Galpón 2",
                    "room": _("Salones comunes"),
                    "shift_label": _("Turno diurno"),
                    "tasks": [
                        {
                            "id": "review-lc-g2-common-001",
                            "title": _("Reunión de cierre con recomendaciones"),
                            "priority": {"label": _("Media"), "theme": "success"},
                            "responsible": "Valeria Cuéllar",
                            "status": {
                                "state": "completed",
                                "label": _("Completada"),
                                "details": _("En curso"),
                            },
                            "review": {
                                "state": "awaiting",
                                "label": _("En revisión"),
                                "details": _("Completa el resumen antes de aprobar."),
                            },
                            "description": _(
                                "Recopilar recomendaciones del turno saliente y asignar responsables para seguimiento."
                            ),
                            "evidence_count": 1,
                            "execution_window": _("05 Nov · 07:30 – 08:00"),
                            "tags": [_("Coordinación"), _("Seguimiento")],
                            "recommendation_placeholder": _("Resume los compromisos acordados"),
                        },
                        {
                            "id": "review-lc-g2-common-002",
                            "title": _("Checklist de bioseguridad ingreso visitantes"),
                            "priority": {"label": _("Alta"), "theme": "critical"},
                            "responsible": "Esteban Mora",
                            "status": {
                                "state": "pending",
                                "label": _("Pendiente"),
                                "details": _("Visita programada 05 Nov · 10:00"),
                            },
                            "review": {
                                "state": "not-started",
                                "label": _("A la espera"),
                                "details": _("Se habilitará cuando se marque como completada."),
                            },
                            "description": _(
                                "Validar que visitantes completen el protocolo de ingreso, cambio de prendas y registro fotográfico."
                            ),
                            "evidence_count": 0,
                            "execution_window": _("05 Nov · 09:30 – 11:30"),
                            "tags": [_("Bioseguridad"), _("Visitas")],
                            "recommendation_placeholder": _("Indica qué evidencia esperas recibir"),
                        },
                    ],
                },
            ],
        },
    ]

    leader_review_summary = {
        "total": 0,
        "awaiting": 0,
        "approved": 0,
        "rejected": 0,
        "overdue": 0,
        "completed": 0,
        "pending": 0,
    }
    shift_totals = {"day": 0, "night": 0}
    farm_totals: dict[str, int] = {}
    barn_totals: dict[str, int] = {}

    for day_payload in leader_review_days:
        day_totals = {
            "total": 0,
            "awaiting": 0,
            "approved": 0,
            "rejected": 0,
            "overdue": 0,
            "completed": 0,
            "pending": 0,
        }
        for location in day_payload["locations"]:
            location_totals = {
                "total": 0,
                "awaiting": 0,
                "approved": 0,
                "rejected": 0,
                "overdue": 0,
                "completed": 0,
                "pending": 0,
            }
            for task in location["tasks"]:
                status = (task.get("status") or {}).get("state")
                review_state = (task.get("review") or {}).get("state")

                leader_review_summary["total"] += 1
                location_totals["total"] += 1
                day_totals["total"] += 1

                if status == "completed":
                    leader_review_summary["completed"] += 1
                    location_totals["completed"] += 1
                    day_totals["completed"] += 1
                elif status == "pending":
                    leader_review_summary["pending"] += 1
                    location_totals["pending"] += 1
                    day_totals["pending"] += 1
                elif status == "overdue":
                    leader_review_summary["overdue"] += 1
                    location_totals["overdue"] += 1
                    day_totals["overdue"] += 1

                if review_state in {"awaiting", "missing"}:
                    leader_review_summary["awaiting"] += 1
                    location_totals["awaiting"] += 1
                    day_totals["awaiting"] += 1
                elif review_state == "approved":
                    leader_review_summary["approved"] += 1
                    location_totals["approved"] += 1
                    day_totals["approved"] += 1
                elif review_state == "rejected":
                    leader_review_summary["rejected"] += 1
                    location_totals["rejected"] += 1
                    day_totals["rejected"] += 1

            location["totals"] = location_totals

            task_count = location_totals["total"]
            shift_label = (location.get("shift_label") or "").lower()
            if any(keyword in shift_label for keyword in {"noche", "noct"}):
                shift_totals["night"] += task_count
            else:
                shift_totals["day"] += task_count

            farm_label = location.get("farm") or _("Sin granja")
            barn_label = location.get("barn") or _("Sin galpón")
            farm_totals[farm_label] = farm_totals.get(farm_label, 0) + task_count
            barn_totals[barn_label] = barn_totals.get(barn_label, 0) + task_count

        day_payload["totals"] = day_totals

    leader_review_filters = {
        "shifts": [
            {
                "id": "all",
                "label": _("Todos los turnos"),
                "count": leader_review_summary["total"],
                "is_default": True,
            },
            {
                "id": "day",
                "label": _("Turno diurno"),
                "count": shift_totals.get("day", 0),
            },
            {
                "id": "night",
                "label": _("Turno nocturno"),
                "count": shift_totals.get("night", 0),
            },
        ],
        "farms": [
            {
                "id": "all",
                "label": _("Todas las granjas"),
                "count": leader_review_summary["total"],
                "is_default": True,
            }
        ],
        "barns": [
            {
                "id": "all",
                "label": _("Todos los galpones"),
                "count": leader_review_summary["total"],
                "is_default": True,
            }
        ],
    }

    for farm_label, count in sorted(farm_totals.items()):
        leader_review_filters["farms"].append(
            {
                "id": slugify(farm_label),
                "label": farm_label,
                "count": count,
            }
        )

    for barn_label, count in sorted(barn_totals.items()):
        leader_review_filters["barns"].append(
            {
                "id": slugify(barn_label),
                "label": barn_label,
                "count": count,
            }
        )

    leader_review_reject_reasons = [
        {"id": "missing-evidence", "label": _("No se adjuntó evidencia suficiente")},
        {"id": "repeat-task", "label": _("Repetir tarea · estándares no cumplidos")},
        {"id": "incomplete-data", "label": _("Datos incompletos o inconsistentes")},
        {"id": "other", "label": _("Otra razón (especificar)")},
    ]

    leader_review_tips = [
        _("Aprueba solo cuando confirmes evidencia y consistencia con los estándares."),
        _("Si rechazas, agrega recomendaciones concretas para el siguiente turno."),
        _("Prioriza las tareas vencidas o sin evidencia antes del relevo."),
    ]

    shift_type_value = None
    if isinstance(shift_confirmation_payload, dict):
        shift_type_value = shift_confirmation_payload.get("shift_type")

    can_share_whatsapp_report = shift_type_value == ShiftType.NIGHT

    return {
        "date_label": date_label,
        "active_shift_type": shift_type_value,
        "can_share_whatsapp_report": can_share_whatsapp_report,
        "user": {
            "display_name": display_name,
            "contact_handle": contact_handle,
            "role": role,
            "avatar_initials": initials,
        },
        "goals": {
            "headline": {
                "title": "Meta central del mes",
                "description": "Suma 160 puntos validados para activar el bono productividad y un descanso flexible.",
                "progress_percent": 68,
                "progress_label": "",
                "points_gap_label": "Te faltan 51 pts (≈ 4 tareas claves)",
                "deadline": "Cierra 30 Nov",
                "reward": "Bono $120.000 + descanso flexible",
            },
            "selection": {
                "is_open": True,
                "window_label": "Elige tus metas antes del 08 Nov 11:59 p. m.",
                "window_description": "Durante esta ventana puedes decidir el plan de metas que prefieras. Tú eliges en qué enfocarte según los premios disponibles.",
                "selected_option_id": "productivity-bonus",
                "options": [
                    {
                        "id": "productivity-bonus",
                        "title": "Plan productividad total",
                        "summary": "Ideal si ya vienes con buena racha y puedes sostener el ritmo.",
                        "reward_label": "Bono $120.000 + descanso flexible",
                        "points_required": "160 pts validados",
                        "effort_label": "4 tareas foco extra esta semana",
                        "badges": [
                            {"label": "Mayor premio", "theme": "brand"},
                            {"label": "Racha mínima 5 días", "theme": "neutral"},
                        ],
                        "actions": [
                            {"label": "Quiero esta meta", "action": "select"},
                            {"label": "Ver plan detallado", "action": "details"},
                        ],
                    },
                    {
                        "id": "innovation-pack",
                        "title": "Plan innovación y mejoras",
                        "summary": "Perfecto si lideras iniciativas de mejora y registro de evidencias.",
                        "reward_label": "Bono $90.000 + reconocimiento en comité",
                        "points_required": "120 pts validados",
                        "effort_label": "3 reportes con evidencia aprobada",
                        "badges": [
                            {"label": "Creatividad", "theme": "success"},
                            {"label": "Validación líder", "theme": "neutral"},
                        ],
                        "actions": [
                            {"label": "Quiero esta meta", "action": "select"},
                            {"label": "Ver plan detallado", "action": "details"},
                        ],
                    },
                    {
                        "id": "balanced-shift",
                        "title": "Plan balance descanso-trabajo",
                        "summary": "Suma menos puntos pero asegura descansos estratégicos.",
                        "reward_label": "Bono $70.000 + turno libre a elección",
                        "points_required": "100 pts validados",
                        "effort_label": "Cumplir descansos planificados y 2 extras voluntarias",
                        "badges": [
                            {"label": "Descansos", "theme": "neutral"},
                            {"label": "Flexibilidad", "theme": "brand"},
                        ],
                        "actions": [
                            {"label": "Quiero esta meta", "action": "select"},
                            {"label": "Ver plan detallado", "action": "details"},
                        ],
                    },
                ],
            },
            "items": [
                {
                    "title": "Validar tareas criticas",
                    "progress_percent": 75,
                    "progress_label": "9 de 12 tareas críticas aprobadas",
                    "impact": "+90 pts",
                },
                {
                    "title": "Cumplir descansos planificados",
                    "progress_percent": 40,
                    "progress_label": "2 de 5 descansos equilibrados",
                    "impact": "Evita penalizaciones",
                },
                {
                    "title": "Reportes con evidencia",
                    "progress_percent": 55,
                    "progress_label": "6 de 11 evidencias aceptadas",
                    "impact": "+30 pts al bono",
                },
            ],
        },
        "production_reference": production_reference,
        "production": production,
        "feed_plan": feed_plan,
        "night_mortality": night_mortality,
        "weight_registry": weight_registry_payload,
        "purchases": purchases or {},
        "pending_classification": pending_classification_summary,
        "egg_workflow": egg_workflow,
        "transport_queue": transport_queue,
        "tasks": tasks,
        "daily_assignments": daily_assignment_schedule,
        "leader_review": {
            "title": _("Revisión de tareas ejecutadas"),
            "subtitle": _("Aprueba o devuelve los reportes de tu equipo por turno y ubicación."),
            "summary": leader_review_summary,
            "filters": leader_review_filters,
            "reject_reasons": leader_review_reject_reasons,
            "tips": leader_review_tips,
            "days": leader_review_days,
        },
        "current_shift": {
            "label": "Turno nocturno - Galpon 3",
            "position": "Posicion: Auxiliar operativo",
            "next": "Proximo turno: 05 Nov - 22:00",
            "week": {
                "range_label": "Semana 04 - 09 Nov",
                "days": [
                    {
                        "weekday": "Lun",
                        "date_label": "04 Nov",
                        "shift_label": "Noche 22:00-06:00",
                        "category": "Operaciones - Bioseguridad",
                        "farm": "Granja La Colina",
                        "barn": "Galpon 3",
                        "is_rest": False,
                    },
                    {
                        "weekday": "Mar",
                        "date_label": "05 Nov",
                        "shift_label": "Noche 22:00-06:00",
                        "category": "Operaciones - Bioseguridad",
                        "farm": "Granja La Colina",
                        "barn": "Galpon 3",
                        "is_rest": False,
                    },
                    {
                        "weekday": "Mie",
                        "date_label": "06 Nov",
                        "shift_label": "Descanso programado",
                        "category": "Recuperacion",
                        "farm": None,
                        "barn": None,
                        "is_rest": True,
                    },
                    {
                        "weekday": "Jue",
                        "date_label": "07 Nov",
                        "shift_label": "Descanso programado",
                        "category": "Recuperacion",
                        "farm": None,
                        "barn": None,
                        "is_rest": True,
                    },
                    {
                        "weekday": "Vie",
                        "date_label": "08 Nov",
                        "shift_label": "Dia 06:00-14:00",
                        "category": "Operaciones - Sanidad",
                        "farm": "Granja La Colina",
                        "barn": "Galpon 2",
                        "is_rest": False,
                    },
                    {
                        "weekday": "Sab",
                        "date_label": "09 Nov",
                        "shift_label": "Dia 06:00-14:00",
                        "category": "Operaciones - Sanidad",
                        "farm": "Granja La Colina",
                        "barn": "Galpon 2",
                        "is_rest": False,
                    },
                ],
            },
        },
        "shift_confirmation": shift_confirmation_payload,
        "shift_confirmation_empty": shift_confirmation_empty_payload,
        "scorecard": {
            "points": 122,
            "streak": "Racha vigente: 6 dias cumplidos",
            "extras": "Tareas extra reportadas: 3",
            "penalties": "Incumplimientos: 1 (impacto -18 pts)",
            "next_reward": "A 28 pts de desbloquear descanso adicional",
            "message": "Sigue reportando iniciativas. Cada aporte aprobado suma 15 puntos.",
        },
        "suggestions": [
            {
                "message": "Mantenimiento preventivo ventiladores - pendiente revision del staff.",
                "status": {
                    "label": "En revisión",
                    "theme": "pending",
                    "icon": "clock",
                    "badge_class": "border-amber-200 bg-amber-50 text-amber-700",
                },
            },
            {
                "message": "Mejora en checklist de bioseguridad - aprobado y publicado.",
                "status": {
                    "label": "Aprobada",
                    "theme": "approved",
                    "icon": "check",
                    "badge_class": "border-emerald-200 bg-emerald-50 text-emerald-700",
                },
            },
            {
                "message": "Ajuste de cronograma de limpieza - reprogramada para el siguiente turno.",
                "status": {
                    "label": "Reprogramada",
                    "theme": "rescheduled",
                    "icon": "arrow-path",
                    "badge_class": "border-sky-200 bg-sky-50 text-sky-700",
                },
            },
            {
                "message": "Propuesta de puntos adicionales en descanso - rechazada por comité.",
                "status": {
                    "label": "Rechazada",
                    "theme": "rejected",
                    "icon": "x-mark",
                    "badge_class": "border-rose-200 bg-rose-50 text-rose-700",
                },
            },
        ],
        "history": [
            {"label": "03 Nov", "summary": "3 tareas completadas - 1 postergada"},
            {"label": "02 Nov", "summary": "4 tareas completadas"},
            {"label": "01 Nov", "summary": "Descanso programado"},
        ],
    }


class TaskManagerMiniAppView(generic.TemplateView):
    """Render the operator experience for the mini app, handling authentication sources."""

    template_name = "task_manager/telegram_mini_app.html"
    form_class = MiniAppAuthenticationForm

    def dispatch(self, request, *args, **kwargs):
        self.mini_app_client = _resolve_mini_app_client(request)
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        form = self.form_class(request=request, data=request.POST)
        if form.is_valid():
            login(request, form.get_user())
            return redirect(request.path)

        return self.render_to_response(self.get_context_data(form=form))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.localdate()
        user = getattr(self.request, "user", None)

        has_authenticated_user = getattr(user, "is_authenticated", False)
        has_access = has_authenticated_user and user.has_perm("task_manager.access_mini_app")
        card_permissions = _resolve_mini_app_card_permissions(user)

        if not has_access:
            card_permissions = {key: False for key in MINI_APP_CARD_PERMISSION_MAP}

        if has_access:
            display_name = (user.get_full_name() or user.get_username() or "Operario").strip()
            phone_number = getattr(user, "telefono", "") or ""
            phone_number = str(phone_number).strip()
            contact_handle = f"@{phone_number}" if phone_number else "@Sin teléfono"
            role_label = _resolve_primary_group_label(user) or "Operario"
            initials = "".join(part[0] for part in display_name.split() if part).upper()[:2] or "OP"
            shift_card_payload: Optional[dict[str, object]] = None
            shift_empty_payload: Optional[dict[str, object]] = None
            if card_permissions.get("shift_confirmation"):
                shift_card = build_shift_confirmation_card(user=user, reference_date=today)
                if shift_card:
                    shift_card_payload = serialize_shift_confirmation_card(shift_card)
                else:
                    shift_empty = build_shift_confirmation_empty_card(user=user, reference_date=today)
                    if shift_empty:
                        shift_empty_payload = serialize_shift_confirmation_empty_card(shift_empty)
            production_payload: Optional[dict[str, object]] = None
            feed_plan_payload: Optional[dict[str, object]] = None
            night_mortality_payload: Optional[dict[str, object]] = None
            weight_registry_payload: Optional[dict[str, object]] = None
            purchases_payload: Optional[dict[str, object]] = None
            session_token = _resolve_mini_app_session_token(self.request)
            if card_permissions.get("production"):
                registry = build_production_registry(user=user, reference_date=today)
                if registry:
                    production_payload = serialize_production_registry(registry)
                    production_payload["submit_url"] = reverse("task_manager:mini-app-production-records")
            if card_permissions.get("feed_plan"):
                feed_plan = build_feed_plan_card(user=user, reference_date=today)
                if feed_plan:
                    feed_plan_payload = serialize_feed_plan_card(feed_plan)
            if card_permissions.get("night_mortality"):
                mortality_registry = build_night_mortality_registry(user=user)
                if mortality_registry:
                    night_mortality_payload = serialize_night_mortality_registry(mortality_registry)
                    night_mortality_payload["submit_url"] = reverse("task_manager:mini-app-night-mortality")
            if card_permissions.get("weight_registry"):
                weight_submit_url = reverse("task_manager:mini-app-weight-registry")
                registry = build_weight_registry(
                    user=user,
                    reference_date=today,
                    session_token=session_token,
                )
                if registry:
                    weight_registry_payload = serialize_weight_registry(registry)
                    weight_registry_payload["submit_url"] = weight_submit_url
            if card_permissions.get("purchase_overview"):
                overview_payload = _build_purchase_overview_payload(user)
                if overview_payload:
                    purchases_payload = purchases_payload or {}
                    purchases_payload["overview"] = overview_payload
                composer_payload = _build_purchase_request_composer_payload(user, self.request)
                if composer_payload:
                    purchases_payload = purchases_payload or {}
                    purchases_payload["composer"] = composer_payload
            if card_permissions.get("purchase_approval"):
                approval_payload = _build_purchase_approval_payload(user, self.request)
                if approval_payload:
                    purchases_payload = purchases_payload or {}
                    purchases_payload["approvals"] = approval_payload
            if card_permissions.get("purchase_management"):
                management_payload = _build_purchase_management_payload(user, self.request)
                if management_payload:
                    purchases_payload = purchases_payload or {}
                    purchases_payload["management"] = management_payload
            mini_app_payload = _build_telegram_mini_app_payload(
                date_label=date_format(today, "DATE_FORMAT"),
                display_name=display_name,
                contact_handle=contact_handle,
                role=role_label,
                initials=initials,
                shift_confirmation=shift_card_payload,
                shift_confirmation_empty=shift_empty_payload,
                include_shift_confirmation_stub=False,
                user=user,
                production=production_payload,
                feed_plan=feed_plan_payload,
                night_mortality=night_mortality_payload,
                weight_registry=weight_registry_payload,
                include_weight_registry=bool(card_permissions.get("weight_registry")),
                purchases=purchases_payload,
            )
            _filter_egg_workflow_stages(mini_app_payload, card_permissions)
            context["telegram_mini_app"] = mini_app_payload
            context["mini_app_logout_url"] = reverse("task_manager:telegram-mini-app-logout")
        else:
            context["telegram_mini_app"] = None
            context["mini_app_logout_url"] = None

        if not has_access:
            form = kwargs.get("form") or self.form_class(request=self.request)
            context["mini_app_form"] = form
        else:
            context["mini_app_form"] = None

        context["mini_app_client"] = self.mini_app_client.value
        context["telegram_integration_enabled"] = self.mini_app_client == MiniAppClient.TELEGRAM
        context["telegram_auth_error"] = None
        context["mini_app_access_granted"] = has_access
        context["mini_app_card_permissions"] = card_permissions
        context["mini_app_pwa_config"] = _build_mini_app_pwa_config()

        return context


class TaskManagerTelegramMiniAppDemoView(generic.TemplateView):
    """Render a simplified, unauthenticated preview of the Telegram mini app."""

    template_name = "task_manager/telegram_mini_app.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.localdate()
        display_name = "Operario demo"
        initials = "".join(part[0] for part in display_name.split() if part).upper()[:2] or "OP"
        card_permissions = _resolve_mini_app_card_permissions(None, force_allow=True)
        demo_payload = _build_telegram_mini_app_payload(
            date_label=date_format(today, "DATE_FORMAT"),
            display_name=display_name,
            contact_handle="@demo",
            role="Vista previa",
            initials=initials,
        )
        _filter_egg_workflow_stages(demo_payload, card_permissions)
        context["telegram_mini_app"] = demo_payload
        context["telegram_integration_enabled"] = False
        context["mini_app_card_permissions"] = card_permissions
        context["mini_app_pwa_config"] = _build_mini_app_pwa_config()
        return context


class MiniAppPushTestView(StaffRequiredMixin, generic.FormView):
    template_name = "task_manager/push_test.html"
    form_class = MiniAppPushTestForm
    success_url = reverse_lazy("task_manager:mini-app-push-test")

    def get_initial(self):
        initial = super().get_initial()
        if user_id := self.request.GET.get("user"):
            initial["user"] = user_id
        if subscription_id := self.request.GET.get("subscription"):
            initial["subscription"] = subscription_id
        return initial

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        selected_user_id = self.request.POST.get("user") or self.request.GET.get("user")
        if selected_user_id:
            kwargs.setdefault("initial", {}).setdefault("user", selected_user_id)
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        selected_user_id = self.request.POST.get("user") or self.request.GET.get("user")
        if selected_user_id:
            context["selected_user_subscriptions"] = MiniAppPushSubscription.objects.filter(
                user_id=selected_user_id
            ).order_by("-updated_at")
        else:
            context["selected_user_subscriptions"] = MiniAppPushSubscription.objects.none()
        context["has_public_key"] = bool(settings.WEB_PUSH_PUBLIC_KEY)
        context["has_private_key"] = bool(settings.WEB_PUSH_PRIVATE_KEY)
        return context

    def form_valid(self, form):
        if not settings.WEB_PUSH_PUBLIC_KEY or not settings.WEB_PUSH_PRIVATE_KEY:
            form.add_error(
                None,
                _(
                    "Configura WEB_PUSH_PUBLIC_KEY y WEB_PUSH_PRIVATE_KEY en el entorno antes de enviar notificaciones."
                ),
            )
            return self.form_invalid(form)

        subscription = form.cleaned_data["subscription"]
        payload = self._build_payload(form.cleaned_data)
        subscription_info = {
            "endpoint": subscription.endpoint,
            "keys": {
                "p256dh": subscription.p256dh_key,
                "auth": subscription.auth_key,
            },
        }

        try:
            webpush(
                subscription_info=subscription_info,
                data=json.dumps(payload),
                vapid_private_key=settings.WEB_PUSH_PRIVATE_KEY,
                vapid_claims={
                    "sub": settings.WEB_PUSH_CONTACT or "mailto:soporte@lacolina.com",
                },
                ttl=form.cleaned_data.get("ttl") or 300,
            )
        except WebPushException as exc:
            error_payload = getattr(exc, "response", None)
            error_text = ""
            if error_payload is not None:
                try:
                    error_text = error_payload.text
                except Exception:  # noqa: BLE001
                    error_text = str(error_payload)
            form.add_error(
                None,
                _("No se pudo enviar la notificación: %(error)s %(details)s")
                % {"error": exc, "details": error_text},
            )
            return self.form_invalid(form)

        messages.success(
            self.request,
            _("Notificación enviada correctamente al dispositivo seleccionado."),
        )
        return super().form_valid(form)

    def get_success_url(self):
        url = super().get_success_url()
        user_id = self.request.POST.get("user")
        if user_id:
            return f"{url}?user={user_id}"
        return url

    def _build_payload(self, cleaned_data: dict[str, object]) -> dict[str, object]:
        data_payload = cleaned_data.get("data_payload") or {}
        if not isinstance(data_payload, dict):
            data_payload = {}
        action_url_raw = cleaned_data.get("action_url") or "/task-manager/telegram/mini-app/"
        action_url = str(action_url_raw).strip() or "/task-manager/telegram/mini-app/"
        data_payload = {**data_payload}
        if action_url:
            data_payload.setdefault("url", action_url)

        payload: dict[str, object] = {
            "title": cleaned_data.get("title") or "Granjas La Colina",
            "body": cleaned_data.get("body") or "",
            "data": data_payload,
        }
        if icon := cleaned_data.get("icon_url"):
            payload["icon"] = icon
        if badge := cleaned_data.get("badge_url"):
            payload["badge"] = badge
        if tag := cleaned_data.get("tag"):
            payload["tag"] = tag
        payload["requireInteraction"] = bool(cleaned_data.get("require_interaction"))
        return payload


@require_POST
def mini_app_task_complete_view(request, pk: int):
    guard = _mini_app_json_guard(request)
    if guard:
        return guard

    user = cast(UserProfile, request.user)
    assignment = _get_user_assignment_for_mini_app(pk, user=user)

    if assignment.completed_on:
        return JsonResponse(
            {
                "status": "completed",
                "assignment_id": assignment.pk,
                "completed_on": assignment.completed_on.isoformat(),
                "already_completed": True,
                "requires_evidence": (
                    assignment.task_definition.evidence_requirement
                    or TaskDefinition.EvidenceRequirement.NONE
                )
                != TaskDefinition.EvidenceRequirement.NONE,
                "completion_note": assignment.completion_note,
                "reset_url": reverse("task_manager:mini-app-task-reset", kwargs={"pk": assignment.pk}),
                "removed": False,
            }
        )

    requires_evidence = (
        assignment.task_definition.evidence_requirement or TaskDefinition.EvidenceRequirement.NONE
    ) != TaskDefinition.EvidenceRequirement.NONE

    if requires_evidence and not assignment.evidences.exists():
        return JsonResponse(
            {"error": _("Debes adjuntar evidencia antes de completar la tarea.")},
            status=400,
        )

    completion_date = timezone.localdate()
    completion_note = ""
    if request.body:
        try:
            payload = json.loads(request.body.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            payload = {}
        else:
            raw_completed_on = payload.get("completed_on")
            if isinstance(raw_completed_on, str):
                try:
                    completion_date = datetime.fromisoformat(raw_completed_on).date()
                except ValueError:
                    completion_date = timezone.localdate()
            raw_note = payload.get("note")
            if isinstance(raw_note, str):
                completion_note = raw_note.strip()[:280]

    assignment.completed_on = completion_date
    assignment.completion_note = completion_note
    assignment.save(update_fields=["completed_on", "completion_note", "updated_at"])

    return JsonResponse(
        {
            "status": "completed",
            "assignment_id": assignment.pk,
            "completed_on": assignment.completed_on.isoformat(),
            "requires_evidence": requires_evidence,
            "reset_url": reverse("task_manager:mini-app-task-reset", kwargs={"pk": assignment.pk}),
            "completion_note": assignment.completion_note,
            "removed": False,
        }
    )


@require_POST
def mini_app_task_evidence_upload_view(request, pk: int):
    guard = _mini_app_json_guard(request)
    if guard:
        return guard

    user = cast(UserProfile, request.user)
    assignment = _get_user_assignment_for_mini_app(pk, user=user)

    files = request.FILES.getlist("evidence")
    if not files:
        files = request.FILES.getlist("files")
    if not files and request.FILES:
        files = list(request.FILES.values())

    if not files:
        return JsonResponse({"error": _("No se recibió archivo de evidencia.")}, status=400)

    note = (request.POST.get("note") or "").strip()
    saved_files: list[dict[str, object]] = []

    for uploaded_file in files:
        evidence = TaskAssignmentEvidence(
            assignment=assignment,
            file=uploaded_file,
            note=note,
            uploaded_by=user,
        )
        evidence.save()
        file_url = evidence.file.url if evidence.file and hasattr(evidence.file, "url") else ""
        saved_files.append(
            {
                "id": evidence.pk,
                "url": file_url,
                "media_type": evidence.media_type,
                "note": evidence.note,
                "uploaded_at": evidence.uploaded_at.isoformat(),
            }
        )

    evidence_count = assignment.evidences.count()
    requires_evidence = (
        assignment.task_definition.evidence_requirement or TaskDefinition.EvidenceRequirement.NONE
    ) != TaskDefinition.EvidenceRequirement.NONE

    return JsonResponse(
        {
            "status": "ok",
            "assignment_id": assignment.pk,
            "evidence_count": evidence_count,
            "requires_evidence": requires_evidence,
            "files": saved_files,
        }
    )


@require_POST
def mini_app_task_reset_view(request, pk: int):
    guard = _mini_app_json_guard(request)
    if guard:
        return guard

    user = cast(UserProfile, request.user)
    assignment = _get_user_assignment_for_mini_app(pk, user=user)

    if not assignment.completed_on:
        return JsonResponse(
            {"error": _("La tarea no está marcada como completada.")},
            status=400,
        )

    assignment.completed_on = None
    assignment.completion_note = ""
    assignment.save(update_fields=["completed_on", "completion_note", "updated_at"])

    return JsonResponse(
        {
            "status": "reset",
            "assignment_id": assignment.pk,
            "completed_on": None,
            "completion_note": "",
        }
    )


@require_POST
def mini_app_push_subscription_view(request):
    guard = _mini_app_json_guard(request)
    if guard:
        return guard

    try:
        payload = json.loads(request.body or b"{}")
    except (TypeError, ValueError):
        return JsonResponse({"error": _("No pudimos procesar la suscripción enviada.")}, status=400)

    subscription_payload = payload.get("subscription")
    if not isinstance(subscription_payload, Mapping):
        return JsonResponse({"error": _("Suscripción inválida.")}, status=400)

    endpoint = subscription_payload.get("endpoint")
    keys = subscription_payload.get("keys") or {}
    p256dh_key = keys.get("p256dh")
    auth_key = keys.get("auth")
    content_encoding = (
        subscription_payload.get("contentEncoding")
        or payload.get("contentEncoding")
        or "aes128gcm"
    )

    if not endpoint or not p256dh_key or not auth_key:
        return JsonResponse({"error": _("Faltan datos obligatorios de la suscripción.")}, status=400)

    expiration_at = _coerce_subscription_expiration(subscription_payload.get("expirationTime"))
    client_label = _resolve_mini_app_client(request).value
    user_agent = (request.META.get("HTTP_USER_AGENT") or "")[:255]

    subscription, created = MiniAppPushSubscription.objects.update_or_create(
        endpoint=endpoint,
        defaults={
            "user": request.user,
            "client": client_label,
            "p256dh_key": p256dh_key,
            "auth_key": auth_key,
            "content_encoding": content_encoding,
            "expiration_time": expiration_at,
            "user_agent": user_agent,
            "is_active": True,
        },
    )

    status_code = 201 if created else 200
    return JsonResponse(
        {
            "status": "ok",
            "subscription_id": subscription.pk,
            "created": created,
        },
        status=status_code,
    )


@require_POST
def mini_app_purchase_request_view(request):
    guard = _mini_app_json_guard(request)
    if guard:
        return guard

    user = cast(UserProfile, request.user)
    if not user.has_perm("task_manager.view_mini_app_purchase_overview_card"):
        return JsonResponse(
            {"error": _("No tienes permisos para registrar solicitudes de compra desde la mini app.")},
            status=403,
        )

    try:
        payload_data = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        payload_data = {}

    intent = (_normalize_string(payload_data.get("intent")) or "save_draft").lower()
    if intent not in {"save_draft", "send_workflow"}:
        intent = "save_draft"

    payload, field_errors, item_errors = _build_mini_app_purchase_request_payload(payload_data, user=user)
    if field_errors or item_errors or payload is None:
        return JsonResponse(
            {
                "error": _("Revisa la información ingresada antes de continuar."),
                "field_errors": field_errors,
                "item_errors": item_errors,
            },
            status=400,
        )

    service = PurchaseRequestSubmissionService(actor=user)
    try:
        purchase = service.submit(payload=payload, intent=intent)
    except PurchaseRequestValidationError as exc:
        return JsonResponse(
            {
                "error": _("No pudimos guardar la solicitud."),
                "field_errors": exc.field_errors,
                "item_errors": exc.item_errors,
            },
            status=400,
        )

    overview_payload = _build_purchase_overview_payload(user)
    composer_payload = _build_purchase_request_composer_payload(user, request)

    message = _("Solicitud enviada a aprobación.") if intent == "send_workflow" else _("Solicitud guardada en borrador.")

    return JsonResponse(
        {
            "status": "ok",
            "message": message,
            "purchase": _serialize_purchase_summary(purchase),
            "requests": overview_payload,
            "composer": composer_payload,
        }
    )


def mini_app_purchase_supplier_search_view(request):
    guard = _mini_app_json_guard(request)
    if guard:
        return guard

    user = cast(UserProfile, request.user)
    if not user.has_perm("task_manager.view_mini_app_purchase_overview_card"):
        return JsonResponse({"error": _("No tienes permisos para consultar terceros.")}, status=403)

    query = _normalize_string(request.GET.get("q"))
    suppliers = Supplier.objects.all()
    if query:
        suppliers = suppliers.filter(Q(name__icontains=query) | Q(tax_id__icontains=query))
    suppliers = suppliers.order_by("name")[:MINI_APP_PURCHASE_SUPPLIER_LIMIT]

    results = []
    for supplier in suppliers:
        display = supplier.name
        if supplier.tax_id:
            display = f"{supplier.name} · {supplier.tax_id}"
        results.append(
            {
                "id": supplier.pk,
                "label": supplier.name,
                "tax_id": supplier.tax_id or "",
                "city": supplier.city or "",
                "display": display,
            }
        )
    return JsonResponse({"results": results})


@require_POST
def mini_app_purchase_supplier_create_view(request):
    guard = _mini_app_json_guard(request)
    if guard:
        return guard

    user = cast(UserProfile, request.user)
    if not user.has_perm("task_manager.view_mini_app_purchase_overview_card"):
        return JsonResponse({"error": _("No tienes permisos para registrar terceros.")}, status=403)

    try:
        payload = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        payload = {}

    form_data = {
        "name": _normalize_string(payload.get("name")),
        "tax_id": _normalize_string(payload.get("tax_id")),
        "contact_name": _normalize_string(payload.get("contact_name")),
        "contact_email": _normalize_string(payload.get("contact_email")),
        "contact_phone": _normalize_string(payload.get("contact_phone")),
        "address": _normalize_string(payload.get("address")),
        "city": _normalize_string(payload.get("city")),
        "account_holder_id": _normalize_string(payload.get("account_holder_id")),
        "account_holder_name": _normalize_string(payload.get("account_holder_name")),
        "account_type": _normalize_string(payload.get("account_type")),
        "account_number": _normalize_string(payload.get("account_number")),
        "bank_name": _normalize_string(payload.get("bank_name")),
    }
    if not form_data["account_holder_id"] and form_data["tax_id"]:
        form_data["account_holder_id"] = form_data["tax_id"]
    if not form_data["account_holder_name"] and form_data["name"]:
        form_data["account_holder_name"] = form_data["name"]

    form = SupplierForm(form_data)
    if form.is_valid():
        supplier = form.save()
        display = supplier.name
        if supplier.tax_id:
            display = f"{supplier.name} · {supplier.tax_id}"
        composer_payload = _build_purchase_request_composer_payload(user, request)
        return JsonResponse(
            {
                "supplier": {
                    "id": supplier.pk,
                    "label": supplier.name,
                    "tax_id": supplier.tax_id or "",
                    "city": supplier.city or "",
                    "display": display,
                },
                "composer": composer_payload,
            },
            status=201,
        )

    errors = {
        field: [str(error) for error in error_list]
        for field, error_list in form.errors.items()
    }
    return JsonResponse(
        {
            "error": _("Revisa los datos del tercero antes de continuar."),
            "field_errors": errors,
        },
        status=400,
    )


def mini_app_purchase_request_modify_view(request, pk: int):
    guard = _mini_app_json_guard(request)
    if guard:
        return guard

    user = cast(UserProfile, request.user)
    if not user.has_perm("task_manager.view_mini_app_purchase_management_card"):
        return JsonResponse(
            {"error": _("No tienes permisos para solicitar modificaciones.")},
            status=403,
        )

    purchase = (
        PurchaseRequest.objects.filter(pk=pk)
        .filter(Q(requester=user) | Q(assigned_manager=user))
        .first()
    )
    if not purchase:
        return JsonResponse({"error": _("No encontramos la solicitud seleccionada.")}, status=404)

    if purchase.status == PurchaseRequest.Status.DRAFT:
        return JsonResponse({"error": _("La solicitud ya está en borrador.")}, status=400)

    try:
        payload = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        payload = {}

    reason = (payload.get("reason") or payload.get("message") or "").strip()
    purchase.status = PurchaseRequest.Status.DRAFT
    update_fields = ["status", "updated_at"]
    if reason:
        timestamp = date_format(timezone.localtime(), "SHORT_DATETIME_FORMAT")
        note = f"[Mini app] {timestamp} · {reason}"
        existing = (purchase.shipping_notes or "").strip()
        purchase.shipping_notes = f"{note}\n\n{existing}".strip() if existing else note
        update_fields.append("shipping_notes")

    purchase.save(update_fields=update_fields)

    notify_purchase_returned_for_changes(
        purchase=purchase,
        manager=user,
        reason=reason,
    )

    overview_payload = _build_purchase_overview_payload(user)
    management_payload = _build_purchase_management_payload(user, request)

    return JsonResponse(
        {
            "status": "ok",
            "message": _("La solicitud volvió a borrador para ser ajustada."),
            "purchase": _serialize_purchase_summary(purchase),
            "requests": overview_payload,
            "management": management_payload,
            "approvals": approval_payload,
        }
    )


@require_POST
def mini_app_purchase_order_view(request, pk: int):
    guard = _mini_app_json_guard(request)
    if guard:
        return guard

    user = cast(UserProfile, request.user)
    if not user.has_perm("task_manager.view_mini_app_purchase_management_card"):
        return JsonResponse(
            {"error": _("No tienes permisos para gestionar compras desde la mini app.")},
            status=403,
        )

    purchase = (
        PurchaseRequest.objects.filter(pk=pk)
        .filter(Q(requester=user) | Q(assigned_manager=user))
        .first()
    )
    if not purchase:
        return JsonResponse({"error": _("No encontramos la solicitud seleccionada.")}, status=404)

    try:
        payload_data = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        payload_data = {}

    intent = (_normalize_string(payload_data.get("intent")) or "save_order").lower()
    if intent not in {"save_order", "confirm_order"}:
        intent = "save_order"

    payload, field_errors = _build_mini_app_order_payload(payload_data, purchase_id=purchase.pk)
    if field_errors or payload is None:
        return JsonResponse(
            {
                "error": _("Revisa la información ingresada antes de continuar."),
                "field_errors": field_errors,
            },
            status=400,
        )

    service = PurchaseOrderService(actor=user)
    try:
        purchase = service.save(payload=payload, intent=intent)
    except PurchaseOrderValidationError as exc:
        return JsonResponse(
            {
                "error": _("No pudimos guardar la gestión de compra."),
                "field_errors": exc.field_errors,
            },
            status=400,
        )

    overview_payload = _build_purchase_overview_payload(user)
    management_payload = _build_purchase_management_payload(user, request)
    approval_payload = _build_purchase_approval_payload(user, request)
    message = _("Información de la compra guardada.")
    if intent == "confirm_order":
        message = _("Compra gestionada. Continúa con la recepción cuando corresponda.")

    return JsonResponse(
        {
            "status": "ok",
            "message": message,
            "purchase": _serialize_purchase_summary(purchase),
            "requests": overview_payload,
            "management": management_payload,
            "approvals": approval_payload,
        }
    )


@require_POST
def mini_app_purchase_finalize_view(request, pk: int):
    guard = _mini_app_json_guard(request)
    if guard:
        return guard

    user = cast(UserProfile, request.user)
    if not user.has_perm("task_manager.view_mini_app_purchase_management_card"):
        return JsonResponse(
            {"error": _("No tienes permisos para finalizar la gestión de compras.")},
            status=403,
        )

    purchase = (
        PurchaseRequest.objects.filter(pk=pk)
        .filter(Q(requester=user) | Q(assigned_manager=user))
        .first()
    )
    if not purchase:
        return JsonResponse({"error": _("No encontramos la solicitud seleccionada.")}, status=404)

    if purchase.status not in {PurchaseRequest.Status.APPROVED, PurchaseRequest.Status.RECEPTION}:
        return JsonResponse(
            {"error": _("Esta solicitud ya no puede marcarse como gestionada.")},
            status=400,
        )

    if purchase.status == PurchaseRequest.Status.APPROVED:
        purchase.status = PurchaseRequest.Status.RECEPTION
    if not purchase.order_date:
        purchase.order_date = timezone.localdate()
    purchase.save(update_fields=["status", "order_date", "updated_at"])

    overview_payload = _build_purchase_overview_payload(user)
    management_payload = _build_purchase_management_payload(user, request)

    return JsonResponse(
        {
            "status": "ok",
            "message": _("Marcaste la solicitud como gestionada. Ahora puedes coordinar la recepción."),
            "purchase": _serialize_purchase_summary(purchase),
            "requests": overview_payload,
            "management": management_payload,
            "approvals": approval_payload,
        }
    )


@require_POST
def mini_app_purchase_approval_view(request, pk: int):
    guard = _mini_app_json_guard(request)
    if guard:
        return guard

    user = cast(UserProfile, request.user)
    if not user.has_perm("task_manager.view_mini_app_purchase_approval_card"):
        return JsonResponse(
            {"error": _("No tienes permisos para aprobar solicitudes desde la mini app.")},
            status=403,
        )

    purchase = PurchaseRequest.objects.filter(pk=pk).first()
    if not purchase:
        return JsonResponse({"error": _("No encontramos la solicitud seleccionada.")}, status=404)

    try:
        payload = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        payload = {}

    assigned_manager_id = _coerce_int(payload.get("assigned_manager_id"))
    if not assigned_manager_id:
        return JsonResponse(
            {"error": _("Selecciona el gestor asignado antes de continuar.")},
            status=400,
        )

    assigned_manager = UserProfile.objects.filter(pk=assigned_manager_id).first()
    if not assigned_manager:
        return JsonResponse({"error": _("Selecciona un gestor válido.")}, status=400)

    decision = (_normalize_string(payload.get("decision")) or "approve").lower()
    if decision not in {"approve", "reject"}:
        decision = "approve"
    note = (payload.get("note") or "").strip()

    manager_notification_target: UserProfile | None = None
    if purchase.assigned_manager_id != assigned_manager_id:
        purchase.assigned_manager_id = assigned_manager_id
        purchase.save(update_fields=["assigned_manager", "updated_at"])
        manager_notification_target = assigned_manager

    service = PurchaseApprovalDecisionService(
        purchase_request=purchase,
        actor=user,
    )

    try:
        if decision == "approve":
            result = service.approve(note=note)
        else:
            result = service.reject(note=note)
    except PurchaseApprovalDecisionError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    purchase.refresh_from_db()
    overview_payload = _build_purchase_overview_payload(user)
    management_payload = _build_purchase_management_payload(user, request)
    approval_payload = _build_purchase_approval_payload(user, request)

    if result.decision == "rejected":
        message = _("Solicitud rechazada y devuelta a borrador.")
    elif result.workflow_completed:
        message = _("Solicitud aprobada completamente.")
    else:
        message = _("Tu aprobación fue registrada. El flujo continuará con el siguiente aprobador.")

    if manager_notification_target:
        notify_purchase_manager_assignment(
            purchase=purchase,
            manager=manager_notification_target,
            source="approval-view",
        )

    if result.workflow_completed or result.decision == "rejected":
        notify_purchase_workflow_result(
            purchase=purchase,
            decision=result.decision,
            workflow_completed=result.workflow_completed,
            approver=user,
        )

    return JsonResponse(
        {
            "status": "ok",
            "message": message,
            "decision": result.decision,
            "purchase": _serialize_purchase_summary(purchase),
            "requests": overview_payload,
            "management": management_payload,
            "approvals": approval_payload,
        }
    )


@require_POST
def mini_app_production_record_view(request):
    guard = _mini_app_json_guard(request)
    if guard:
        return guard

    user = cast(UserProfile, request.user)
    if not user.has_perm("task_manager.view_mini_app_production_card"):
        return JsonResponse(
            {"error": _("No tienes permisos para registrar la producción diaria.")},
            status=403,
        )

    try:
        payload = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": _("Formato de solicitud inválido.")}, status=400)

    date_str = payload.get("date")
    target_date = None
    if date_str:
        try:
            target_date = date.fromisoformat(str(date_str))
        except ValueError:
            return JsonResponse({"error": _("La fecha del registro no es válida.")}, status=400)

    registry = build_production_registry(user=user, reference_date=target_date or timezone.localdate())
    if not registry:
        return JsonResponse(
            {"error": _("No encontramos lotes activos asociados a tu posición actual.")},
            status=404,
        )

    if target_date and registry.date != target_date:
        return JsonResponse(
            {"error": _("La fecha enviada no coincide con el turno activo para registro.")},
            status=400,
        )

    entries = payload.get("lots")
    if not isinstance(entries, list):
        return JsonResponse({"error": _("Debes enviar los registros por lote.")}, status=400)

    try:
        persist_production_records(registry=registry, entries=entries, user=user)
    except ValidationError as exc:
        message = _extract_validation_message(exc)
        return JsonResponse({"error": message}, status=400)

    updated_registry = build_production_registry(user=user, reference_date=registry.date)
    response_payload = None
    if updated_registry:
        response_payload = serialize_production_registry(updated_registry)
        response_payload["submit_url"] = reverse("task_manager:mini-app-production-records")

    return JsonResponse(
        {
            "status": "ok",
            "production": response_payload,
        }
    )


@require_POST
def mini_app_night_mortality_view(request):
    guard = _mini_app_json_guard(request)
    if guard:
        return guard

    user = cast(UserProfile, request.user)
    if not user.has_perm("task_manager.view_mini_app_task_cards"):
        return JsonResponse(
            {"error": _("No tienes permisos para registrar la mortalidad nocturna.")},
            status=403,
        )

    try:
        payload = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": _("Formato de solicitud inválido.")}, status=400)

    date_str = payload.get("date")
    target_date: Optional[date] = None
    if date_str:
        try:
            target_date = date.fromisoformat(str(date_str))
        except ValueError:
            return JsonResponse({"error": _("La fecha del registro no es válida.")}, status=400)

    if target_date is not None:
        registry = build_night_mortality_registry(user=user, registry_date=target_date)
    else:
        registry = build_night_mortality_registry(user=user)
    if not registry:
        return JsonResponse(
            {"error": _("No encontramos lotes o galpones activos en tu turno nocturno.")},
            status=404,
        )

    if target_date is not None and registry.date != target_date:
        return JsonResponse(
            {"error": _("La fecha enviada no coincide con el turno nocturno activo.")},
            status=400,
        )

    entries = payload.get("lots")
    if not isinstance(entries, list):
        return JsonResponse({"error": _("Debes enviar los registros por lote.")}, status=400)

    try:
        persist_night_mortality_entries(
            registry=registry,
            entries=entries,
            user=user,
        )
    except ValidationError as exc:
        message = _extract_validation_message(exc)
        return JsonResponse({"error": message}, status=400)

    refreshed = build_night_mortality_registry(user=user, registry_date=registry.date)
    response_payload = None
    if refreshed:
        response_payload = serialize_night_mortality_registry(refreshed)
        response_payload["submit_url"] = reverse("task_manager:mini-app-night-mortality")

    return JsonResponse(
        {
            "status": "ok",
            "night_mortality": response_payload,
        }
    )


@require_POST
def mini_app_weight_registry_view(request):
    guard = _mini_app_json_guard(request)
    if guard:
        return guard

    user = cast(UserProfile, request.user)
    if not user.has_perm("task_manager.view_mini_app_weight_registry_card"):
        return JsonResponse(
            {"error": _("No tienes permisos para registrar los pesos.")},
            status=403,
        )

    session_token = _resolve_mini_app_session_token(request)

    try:
        payload = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": _("Formato de solicitud inválido.")}, status=400)

    date_str = payload.get("date")
    target_date = timezone.localdate()
    if date_str:
        try:
            target_date = date.fromisoformat(str(date_str))
        except ValueError:
            return JsonResponse({"error": _("La fecha enviada no es válida.")}, status=400)

    registry = build_weight_registry(
        user=user,
        reference_date=target_date,
        session_token=session_token,
    )
    if not registry:
        return JsonResponse(
            {"error": _("No encontramos salones asignados a tu turno.")},
            status=404,
        )

    if registry.date != target_date:
        return JsonResponse(
            {"error": _("La fecha enviada no coincide con el turno activo.")},
            status=400,
        )

    sessions_payload = payload.get("sessions") or payload.get("session_details")
    if not isinstance(sessions_payload, list):
        return JsonResponse({"error": _("Debes enviar los salones con sus pesos capturados.")}, status=400)

    try:
        persist_weight_registry(
            registry=registry,
            sessions=sessions_payload,
            user=user,
        )
    except ValidationError as exc:
        message = _extract_validation_message(exc)
        return JsonResponse({"error": message}, status=400)

    refreshed = build_weight_registry(
        user=user,
        reference_date=registry.date,
        session_token=session_token,
    )
    payload_response = None
    if refreshed:
        payload_response = serialize_weight_registry(refreshed)
        payload_response["submit_url"] = reverse("task_manager:mini-app-weight-registry")

    return JsonResponse(
        {
            "status": "ok",
            "weight_registry": payload_response,
        }
    )


@require_POST
def mini_app_transport_authorize_view(request):
    guard = _mini_app_json_guard(request)
    if guard:
        return guard

    user = cast(UserProfile, request.user)
    if not user.has_perm("task_manager.view_mini_app_transport_queue_card"):
        return JsonResponse({"error": _("No tienes permisos para autorizar transporte.")}, status=403)

    try:
        payload = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": _("Formato de solicitud inválido.")}, status=400)

    production_ids = payload.get("production_ids")
    transporter_id = payload.get("transporter_id")
    expected_date_str = payload.get("expected_date")

    if not isinstance(production_ids, list) or not production_ids:
        return JsonResponse({"error": _("Selecciona al menos una producción.")}, status=400)
    try:
        production_ids = [int(value) for value in production_ids]
    except (TypeError, ValueError):
        return JsonResponse({"error": _("Los identificadores enviados no son válidos.")}, status=400)

    try:
        transporter = UserProfile.objects.get(pk=int(transporter_id))
    except (UserProfile.DoesNotExist, TypeError, ValueError):
        return JsonResponse({"error": _("El transportador enviado no existe.")}, status=404)

    if not expected_date_str:
        return JsonResponse({"error": _("Debes indicar la fecha estimada de transporte.")}, status=400)
    try:
        expected_date = date.fromisoformat(str(expected_date_str))
    except ValueError:
        return JsonResponse({"error": _("La fecha estimada no es válida.")}, status=400)

    try:
        authorize_internal_transport(
            batch_ids=production_ids,
            transporter=transporter,
            expected_date=expected_date,
            actor=user,
        )
    except ValidationError as exc:
        message = _extract_validation_message(exc)
        return JsonResponse({"error": message}, status=400)

    transport_queue = build_transport_queue_payload()
    transport_queue["submit_url"] = reverse("task_manager:mini-app-transport-authorize")
    transport_stage = build_transport_stage_payload(user=user)
    transport_stage["progress_url"] = reverse("task_manager:mini-app-transport-progress")
    transport_stage["confirmation_url"] = reverse("task_manager:mini-app-transport-confirmation")
    verification_stage = build_transport_verification_payload()
    verification_stage["submit_url"] = reverse("task_manager:mini-app-transport-verification")

    return JsonResponse(
        {
            "status": "ok",
            "transport_queue": transport_queue,
            "transport_stage": transport_stage,
            "verification_stage": verification_stage,
        }
    )


@require_POST
def mini_app_transport_progress_view(request):
    guard = _mini_app_json_guard(request)
    if guard:
        return guard

    user = cast(UserProfile, request.user)
    if not user.has_perm("task_manager.view_mini_app_egg_stage_transport_card"):
        return JsonResponse({"error": _("No tienes permisos para actualizar el transporte interno.")}, status=403)

    try:
        payload = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": _("Formato de solicitud inválido.")}, status=400)

    step = payload.get("step")
    if not isinstance(step, str):
        return JsonResponse({"error": _("Debes indicar el estado del transporte.")}, status=400)

    try:
        update_transport_progress(step=step, actor=user)
    except ValidationError as exc:
        message = _extract_validation_message(exc)
        return JsonResponse({"error": message}, status=400)

    transport_stage = build_transport_stage_payload(user=user)
    transport_stage["progress_url"] = reverse("task_manager:mini-app-transport-progress")
    transport_stage["confirmation_url"] = reverse("task_manager:mini-app-transport-confirmation")

    return JsonResponse({"status": "ok", "transport_stage": transport_stage})


@require_POST
def mini_app_transport_verification_view(request):
    guard = _mini_app_json_guard(request)
    if guard:
        return guard

    user = cast(UserProfile, request.user)
    if not user.has_perm("task_manager.view_mini_app_egg_stage_verification_card"):
        return JsonResponse({"error": _("No tienes permisos para verificar el transporte.")}, status=403)

    try:
        payload = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": _("Formato de solicitud inválido.")}, status=400)

    entries = payload.get("entries")
    if not isinstance(entries, list):
        return JsonResponse({"error": _("Debes enviar las producciones a verificar.")}, status=400)

    try:
        record_transport_verification(entries=entries, actor=user)
    except ValidationError as exc:
        message = _extract_validation_message(exc)
        return JsonResponse({"error": message}, status=400)

    verification_stage = build_transport_verification_payload()
    verification_stage["submit_url"] = reverse("task_manager:mini-app-transport-verification")

    return JsonResponse({"status": "ok", "verification_stage": verification_stage})


@require_POST
def mini_app_transport_confirmation_view(request):
    guard = _mini_app_json_guard(request)
    if guard:
        return guard

    user = cast(UserProfile, request.user)
    if not user.has_perm("task_manager.view_mini_app_egg_stage_transport_card"):
        return JsonResponse({"error": _("No tienes permisos para confirmar el transporte.")}, status=403)

    try:
        payload = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": _("Formato de solicitud inválido.")}, status=400)

    entries = payload.get("entries")
    if not isinstance(entries, list):
        return JsonResponse({"error": _("Debes enviar las producciones a confirmar.")}, status=400)

    try:
        record_transporter_confirmation(entries=entries, actor=user)
    except ValidationError as exc:
        message = _extract_validation_message(exc)
        return JsonResponse({"error": message}, status=400)

    transport_stage = build_transport_stage_payload(user=user)
    transport_stage["progress_url"] = reverse("task_manager:mini-app-transport-progress")
    transport_stage["confirmation_url"] = reverse("task_manager:mini-app-transport-confirmation")

    return JsonResponse({"status": "ok", "transport_stage": transport_stage})


def mini_app_logout_view(request):
    if request.method == "POST":
        logout(request)
        return redirect("task_manager:telegram-mini-app")

    return redirect("task_manager:telegram-mini-app")


telegram_mini_app_view = TaskManagerMiniAppView.as_view()
telegram_mini_app_demo_view = TaskManagerTelegramMiniAppDemoView.as_view()
mini_app_push_test_view = MiniAppPushTestView.as_view()


def _task_definition_redirect_url(task_id: int) -> str:
    base_url = reverse("task_manager:index")
    return f"{base_url}?tm_task={task_id}#tm-tareas"


def _build_duplicate_task_name(original_name: str) -> str:
    """Return a human-friendly name for a duplicated task."""

    base_name = (original_name or "").strip() or _("Tarea sin nombre")
    name_field = TaskDefinition._meta.get_field("name")
    max_length = getattr(name_field, "max_length", 200) or 200
    counter = 1

    while True:
        if counter == 1:
            suffix = _(" (copia)")
        else:
            suffix = _(" (copia %(number)s)") % {"number": counter}

        available = max(max_length - len(suffix), 0)
        truncated_base = base_name[:available] if available else ""
        candidate = f"{truncated_base}{suffix}" if truncated_base else suffix[:max_length]

        if not TaskDefinition.objects.filter(name=candidate).exists():
            return candidate

        counter += 1


def _duplicate_task_definition(task: TaskDefinition) -> TaskDefinition:
    """Persist a cloned version of the provided task definition."""

    duplicate = TaskDefinition.objects.create(
        name=_build_duplicate_task_name(task.name),
        description=task.description,
        status=task.status,
        category=task.category,
        is_mandatory=task.is_mandatory,
        is_accumulative=task.is_accumulative,
        criticality_level=task.criticality_level,
        task_type=task.task_type,
        scheduled_for=task.scheduled_for,
        weekly_days=list(task.weekly_days or []),
        fortnight_days=list(task.fortnight_days or []),
        month_days=list(task.month_days or []),
        monthly_week_days=list(task.monthly_week_days or []),
        position=task.position,
        collaborator=task.collaborator,
        evidence_requirement=task.evidence_requirement,
        record_format=task.record_format,
    )
    room_ids = list(task.rooms.values_list("pk", flat=True))
    if room_ids:
        duplicate.rooms.set(room_ids)
    return duplicate


def serialize_task_definition(task: TaskDefinition) -> dict[str, object]:
    return {
        "id": task.pk,
        "name": task.name,
        "description": task.description,
        "status": task.status_id,
        "category": task.category_id,
        "is_mandatory": task.is_mandatory,
        "is_accumulative": task.is_accumulative,
        "criticality_level": task.criticality_level,
        "task_type": task.task_type,
        "scheduled_for": task.scheduled_for.isoformat() if task.scheduled_for else None,
        "weekly_days": list(task.weekly_days or []),
        "month_days": list(task.month_days or []),
        "position": task.position_id,
        "collaborator": task.collaborator_id,
        "rooms": list(task.rooms.values_list("pk", flat=True)),
        "evidence_requirement": task.evidence_requirement,
        "record_format": task.record_format,
    }


class TaskDefinitionCreateView(StaffRequiredMixin, View):
    """Persist a task definition from the quick-create side panel."""

    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        form = TaskDefinitionQuickCreateForm(request.POST)
        if form.is_valid():
            task_definition: TaskDefinition = form.save()
            payload = {
                "id": task_definition.pk,
                "name": task_definition.name,
                "redirect_url": _task_definition_redirect_url(task_definition.pk),
            }
            return JsonResponse(payload, status=201)

        errors = {
            field: [error.get("message", "") for error in error_list]
            for field, error_list in form.errors.get_json_data().items()
        }
        return JsonResponse({"errors": errors}, status=400)


task_definition_create_view = TaskDefinitionCreateView.as_view()


class TaskDefinitionDetailView(StaffRequiredMixin, View):
    """Return the serialized representation of a task definition."""

    http_method_names = ["get"]

    def get(self, request, pk: int, *args, **kwargs):
        task_definition = get_object_or_404(
            TaskDefinition.objects.select_related("status", "category", "position", "collaborator"),
            pk=pk,
        )
        payload = serialize_task_definition(task_definition)
        return JsonResponse(payload, status=200)


task_definition_detail_view = TaskDefinitionDetailView.as_view()


class TaskDefinitionUpdateView(StaffRequiredMixin, View):
    """Update an existing task definition from the quick-create side panel."""

    http_method_names = ["post"]

    def post(self, request, pk: int, *args, **kwargs):
        task_definition = get_object_or_404(TaskDefinition, pk=pk)
        form = TaskDefinitionQuickCreateForm(request.POST, instance=task_definition)
        if form.is_valid():
            updated_task: TaskDefinition = form.save()
            payload = {
                "id": updated_task.pk,
                "name": updated_task.name,
                "redirect_url": _task_definition_redirect_url(updated_task.pk),
            }
            return JsonResponse(payload, status=200)

        errors = {
            field: [error.get("message", "") for error in error_list]
            for field, error_list in form.errors.get_json_data().items()
        }
        return JsonResponse({"errors": errors}, status=400)


task_definition_update_view = TaskDefinitionUpdateView.as_view()


class TaskDefinitionDuplicateView(StaffRequiredMixin, View):
    """Create a copy of an existing task definition."""

    http_method_names = ["post"]

    def post(self, request, pk: int, *args, **kwargs):
        task_definition = get_object_or_404(
            TaskDefinition.objects.prefetch_related("rooms"),
            pk=pk,
        )
        with transaction.atomic():
            duplicate = _duplicate_task_definition(task_definition)
        messages.success(
            request,
            _('Se creó una copia de "%(source)s" llamada "%(target)s".')
            % {"source": task_definition.name, "target": duplicate.name},
        )
        redirect_url = _task_definition_redirect_url(duplicate.pk)
        return redirect(redirect_url)


task_definition_duplicate_view = TaskDefinitionDuplicateView.as_view()


class TaskDefinitionDeleteView(StaffRequiredMixin, View):
    """Delete an existing task definition after confirmation."""

    http_method_names = ["post"]

    def post(self, request, pk: int, *args, **kwargs):
        task_definition = get_object_or_404(
            TaskDefinition.objects.prefetch_related("assignments"),
            pk=pk,
        )
        task_name = task_definition.name
        assignments_qs = task_definition.assignments.all()
        assignment_count = assignments_qs.count()
        with transaction.atomic():
            if assignment_count:
                assignments_qs.delete()
            task_definition.delete()
        if assignment_count:
            detail = ngettext(
                "Se eliminó %(count)s asignación asociada.",
                "Se eliminaron %(count)s asignaciones asociadas.",
                assignment_count,
            ) % {"count": assignment_count}
            message = _('La tarea "%(name)s" se eliminó correctamente. %(detail)s') % {
                "name": task_name,
                "detail": detail,
            }
        else:
            message = _('La tarea "%(name)s" se eliminó correctamente.') % {"name": task_name}
        messages.success(request, message)
        redirect_url = f"{reverse('task_manager:index')}#tm-tareas"
        return redirect(redirect_url)


task_definition_delete_view = TaskDefinitionDeleteView.as_view()


class TaskDefinitionListView(StaffRequiredMixin, View):
    """Return rendered task rows for incremental loading."""

    http_method_names = ["get"]

    def get(self, request, *args, **kwargs):
        if request.headers.get("X-Requested-With") != "XMLHttpRequest":
            return JsonResponse({"detail": _("Solicitud inválida.")}, status=400)

        filters = build_task_definition_filters(request.GET)
        queryset = get_task_definition_queryset(filters)
        paginator = Paginator(queryset, 400)
        page_number = request.GET.get("page")
        page_obj = paginator.get_page(page_number)
        rows = build_task_definition_rows(page_obj.object_list)
        rows_html = render_to_string(
            "task_manager/includes/task_definition_rows.html",
            {"rows": rows},
            request=request,
        )

        if paginator.count:
            start_index = page_obj.start_index()
            end_index = page_obj.end_index()
        else:
            start_index = 0
            end_index = 0

        payload = {
            "rows_html": rows_html,
            "page": page_obj.number,
            "has_next": page_obj.has_next(),
            "next_page": page_obj.next_page_number() if page_obj.has_next() else None,
            "count": paginator.count,
            "page_size": paginator.per_page,
            "start_index": start_index,
            "end_index": end_index,
            "rows_loaded": len(rows),
        }
        return JsonResponse(payload, status=200)


task_definition_list_view = TaskDefinitionListView.as_view()


class TaskDefinitionReorderView(StaffRequiredMixin, View):
    """Persist manual ordering for task definitions."""

    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        if request.headers.get("Content-Type", "").startswith("application/json"):
            try:
                payload = json.loads(request.body.decode("utf-8"))
            except (TypeError, ValueError, json.JSONDecodeError):
                return JsonResponse({"detail": _("Datos inválidos para reordenar.")}, status=400)
        else:
            payload = request.POST

        order_payload = payload.get("order") if isinstance(payload, dict) else None
        if not isinstance(order_payload, list) or not order_payload:
            return JsonResponse({"detail": _("No se recibió un orden válido.")}, status=400)

        assignments: list[tuple[int, int]] = []
        seen_ids: set[int] = set()
        for item in order_payload:
            if not isinstance(item, dict):
                continue
            task_id = item.get("id")
            order_value = item.get("order")
            try:
                task_id_int = int(task_id)
                order_int = int(order_value)
            except (TypeError, ValueError):
                continue
            if task_id_int in seen_ids:
                continue
            seen_ids.add(task_id_int)
            assignments.append((task_id_int, order_int))

        if not assignments:
            return JsonResponse({"detail": _("No se pudo interpretar el nuevo orden.")}, status=400)

        order_values = [order for _, order in assignments]
        if len(order_values) != len(set(order_values)):
            return JsonResponse({"detail": _("Los valores de orden no pueden repetirse.")}, status=400)

        updated_count = 0
        with transaction.atomic():
            locked_tasks = list(
                TaskDefinition.objects.select_for_update().filter(pk__in=[task_id for task_id, _ in assignments])
            )
            if len(locked_tasks) != len(assignments):
                return JsonResponse({"detail": _("Alguna tarea indicada no existe.")}, status=400)

            task_map = {task.pk: task for task in locked_tasks}
            updated: list[TaskDefinition] = []
            for task_id, order_value in assignments:
                task = task_map.get(task_id)
                if task is None:
                    continue
                if task.display_order != order_value:
                    task.display_order = order_value
                    updated.append(task)

            if updated:
                TaskDefinition.objects.bulk_update(updated, ["display_order"])
                updated_count = len(updated)

        return JsonResponse({"updated": updated_count}, status=200)


task_definition_reorder_view = TaskDefinitionReorderView.as_view()


@dataclass(frozen=True)
class TaskDefinitionRow:
    id: int
    detail_url: str
    update_url: str
    duplicate_url: str
    name: str
    description: str
    category_label: str
    status_label: str
    status_is_active: bool
    is_mandatory: bool
    mandatory_label: str
    mandatory_badge_class: str
    criticality_level: str
    criticality_label: str
    criticality_badge_class: str
    task_type_label: str
    schedule_summary: str
    schedule_segments: Sequence[str]
    schedule_detail: str
    is_one_time: bool
    scheduled_for_display: Optional[str]
    scope_label: str
    scope_level: str
    scope_badge_label: str
    responsible_main: str
    responsible_secondary: str
    evidence_label: str
    has_evidence_requirement: bool
    record_label: str
    requires_record_format: bool
    created_on_display: str
    display_order: int
    group_status: "TaskDefinitionGroupValue"
    group_category: "TaskDefinitionGroupValue"
    group_scope: "TaskDefinitionGroupValue"
    group_responsible: "TaskDefinitionGroupValue"
    group_task_type: "TaskDefinitionGroupValue"
    group_evidence: "TaskDefinitionGroupValue"
    group_mandatory: "TaskDefinitionGroupValue"
    group_criticality: "TaskDefinitionGroupValue"


@dataclass(frozen=True)
class TaskDefinitionGroupValue:
    key: str
    label: str
    subtitle: Optional[str] = None


TASK_FILTER_PARAM_NAMES: tuple[str, ...] = (
    "status",
    "category",
    "mandatory",
    "criticality",
    "scope",
    "responsible",
    "search",
    "scheduled_start",
    "scheduled_end",
)


@dataclass
class TaskDefinitionFilters:
    status: str = "all"
    category: str = "all"
    mandatory: str = "all"
    criticality: str = "all"
    scope: str = "all"
    responsible: str = "all"
    search: str = ""
    scheduled_start: str = ""
    scheduled_end: str = ""


@dataclass(frozen=True)
class ActiveFilterChip:
    key: str
    label: str
    display_value: str
    value: str
    remove_url: str


def get_task_definition_queryset(
    filters: Optional[TaskDefinitionFilters] = None,
) -> QuerySet[TaskDefinition]:
    queryset = (
        TaskDefinition.objects.select_related(
            "status",
            "category",
            "position__farm",
            "position__chicken_house",
            "collaborator",
        )
        .prefetch_related(
            "rooms__chicken_house__farm",
            "position__rooms__chicken_house__farm",
        )
        .order_by("display_order", "name", "pk")
    )

    if filters is None:
        filters = TaskDefinitionFilters()

    return apply_task_definition_filters(queryset, filters)


def build_task_definition_filters(params: Optional[Mapping[str, str] | QueryDict]) -> TaskDefinitionFilters:
    filters = TaskDefinitionFilters()
    if not params:
        return filters

    for key in TASK_FILTER_PARAM_NAMES:
        raw_value = params.get(key)
        if raw_value is None:
            continue
        value = str(raw_value).strip()
        if not value:
            continue
        setattr(filters, key, value)

    return filters


def apply_task_definition_filters(
    queryset: QuerySet[TaskDefinition],
    filters: TaskDefinitionFilters,
) -> QuerySet[TaskDefinition]:
    needs_distinct = False

    status_value = filters.status
    status_id = _parse_positive_int(status_value)
    if status_id is not None:
        queryset = queryset.filter(effective_status_id=status_id)

    category_value = filters.category
    category_id = _parse_positive_int(category_value)
    if category_id is not None:
        queryset = queryset.filter(category_id=category_id)

    mandatory_value = filters.mandatory
    if mandatory_value == "required":
        queryset = queryset.filter(is_mandatory=True)
    elif mandatory_value == "optional":
        queryset = queryset.filter(is_mandatory=False)

    criticality_value = filters.criticality
    if criticality_value and criticality_value not in {"", "all"}:
        queryset = queryset.filter(criticality_level=criticality_value)

    responsible_value = filters.responsible
    if responsible_value and responsible_value not in {"", "all"}:
        if responsible_value == "collaborator":
            queryset = queryset.filter(collaborator__isnull=False)
        elif responsible_value == "position":
            queryset = queryset.filter(position__isnull=False)
        elif responsible_value == "unassigned":
            queryset = queryset.filter(collaborator__isnull=True, position__isnull=True)
        elif responsible_value.startswith("collaborator:"):
            collaborator_id = _parse_positive_int(responsible_value.partition(":")[2])
            if collaborator_id is not None:
                queryset = queryset.filter(collaborator_id=collaborator_id)
        elif responsible_value.startswith("position:"):
            position_id = _parse_positive_int(responsible_value.partition(":")[2])
            if position_id is not None:
                queryset = queryset.filter(position_id=position_id)

    scope_value = filters.scope
    if scope_value and scope_value not in {"", "all"}:
        needs_distinct = True
        prefix, _, suffix = scope_value.partition(":")
        scope_id = _parse_positive_int(suffix) if suffix else None

        if prefix == "general":
            queryset = queryset.filter(
                rooms__isnull=True,
                position__rooms__isnull=True,
                position__farm__isnull=True,
                position__chicken_house__isnull=True,
            )
        elif prefix == "farm":
            base_filter = Q(rooms__chicken_house__farm__isnull=False) | Q(
                position__rooms__chicken_house__farm__isnull=False
            ) | Q(position__farm__isnull=False)
            if scope_id is None:
                queryset = queryset.filter(base_filter)
            else:
                queryset = queryset.filter(
                    Q(rooms__chicken_house__farm__pk=scope_id)
                    | Q(position__rooms__chicken_house__farm__pk=scope_id)
                    | Q(position__farm_id=scope_id)
                )
        elif prefix == "house":
            base_filter = Q(rooms__chicken_house__isnull=False) | Q(
                position__rooms__chicken_house__isnull=False
            ) | Q(position__chicken_house__isnull=False)
            if scope_id is None:
                queryset = queryset.filter(base_filter)
            else:
                queryset = queryset.filter(
                    Q(rooms__chicken_house__pk=scope_id)
                    | Q(position__rooms__chicken_house__pk=scope_id)
                    | Q(position__chicken_house_id=scope_id)
                )
        elif prefix == "room":
            if scope_id is None:
                queryset = queryset.filter(
                    Q(rooms__isnull=False) | Q(position__rooms__isnull=False)
                )
            else:
                queryset = queryset.filter(
                    Q(rooms__pk=scope_id) | Q(position__rooms__pk=scope_id)
                )
        else:
            needs_distinct = False

    search_value = (filters.search or "").strip()
    if search_value:
        filters.search = search_value
        queryset = queryset.filter(name__icontains=search_value)

    schedule_start = _parse_iso_date(filters.scheduled_start)
    schedule_end = _parse_iso_date(filters.scheduled_end)
    if schedule_start and schedule_end and schedule_end < schedule_start:
        schedule_start, schedule_end = schedule_end, schedule_start
    filters.scheduled_start = schedule_start.isoformat() if schedule_start else ""
    filters.scheduled_end = schedule_end.isoformat() if schedule_end else ""
    if schedule_start and schedule_end:
        queryset = queryset.filter(scheduled_for__gte=schedule_start, scheduled_for__lte=schedule_end)
    elif schedule_start:
        queryset = queryset.filter(scheduled_for=schedule_start)
    elif schedule_end:
        queryset = queryset.filter(scheduled_for__lte=schedule_end)

    if needs_distinct:
        queryset = queryset.distinct()

    return queryset


def _parse_positive_int(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if parsed <= 0:
        return None
    return parsed


def _parse_iso_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def resolve_option_label(groups: Sequence["FilterOptionGroup"], value: str) -> Optional[str]:
    for group in groups:
        for option in group.options:
            if option.value == value:
                return option.label
    return None


def ensure_filter_selection(
    filters: TaskDefinitionFilters,
    field_name: str,
    groups: Sequence["FilterOptionGroup"],
    default_value: str,
) -> tuple[str, str]:
    value = getattr(filters, field_name)
    if not value:
        value = default_value
    label = resolve_option_label(groups, value)
    if label is not None:
        setattr(filters, field_name, value)
        return value, label

    setattr(filters, field_name, default_value)
    fallback_label = resolve_option_label(groups, default_value) or ""
    return default_value, fallback_label


def build_active_filter_chip(
    request,
    key: str,
    label: str,
    display_value: str,
    value: str,
    remove_keys: Optional[Sequence[str]] = None,
) -> ActiveFilterChip:
    params = request.GET.copy()
    params._mutable = True
    keys_to_remove = list(remove_keys or [key])
    for param_key in keys_to_remove:
        if param_key in params:
            params.pop(param_key)
    if "page" in params:
        params.pop("page")
    params["tm_tab"] = "tareas"
    remove_query = params.urlencode()
    base_url = request.path
    remove_url = f"{base_url}?{remove_query}#tm-tareas" if remove_query else f"{base_url}#tm-tareas"
    return ActiveFilterChip(
        key=key,
        label=label,
        display_value=display_value,
        value=value,
        remove_url=remove_url,
    )


def build_clear_filters_url(request) -> str:
    params = request.GET.copy()
    params._mutable = True
    for key in TASK_FILTER_PARAM_NAMES + ("page",):
        if key in params:
            params.pop(key)
    params["tm_tab"] = "tareas"
    query = params.urlencode()
    base_url = request.path
    return f"{base_url}?{query}#tm-tareas" if query else f"{base_url}#tm-tareas"


def build_task_definition_rows(tasks: Optional[Iterable[TaskDefinition]] = None) -> Sequence[TaskDefinitionRow]:
    queryset = tasks if tasks is not None else get_task_definition_queryset()

    rows: list[TaskDefinitionRow] = []
    for task in queryset:
        task_type_label = get_task_type_label(task)
        schedule_summary, schedule_segments = format_task_schedule(task, task_type_label)
        scope_label, scope_level, scope_badge_label = format_task_scope(task)
        responsible_main, responsible_secondary = format_task_responsible(task)
        evidence_label = task.get_evidence_requirement_display()
        record_label = task.get_record_format_display()
        is_one_time = task.task_type == TaskDefinition.TaskType.ONE_TIME
        scheduled_for_display: Optional[str] = None

        if is_one_time:
            if task.scheduled_for:
                scheduled_for_display = date_format(task.scheduled_for, "DATE_FORMAT")
            else:
                scheduled_for_display = _("Sin fecha definida")

        if is_one_time:
            schedule_detail = scheduled_for_display or _("Sin fecha definida")
        else:
            schedule_detail = " · ".join(schedule_segments) if schedule_segments else _("Configuración pendiente")

        effective_status = task.effective_status
        effective_status_id = task.effective_status_id
        if effective_status and getattr(effective_status, "name", ""):
            status_label = effective_status.name
            status_is_active = effective_status.is_active
        elif getattr(task, "status", None) and getattr(task.status, "name", ""):
            status_label = task.status.name
            status_is_active = task.status.is_active
        else:
            status_label = _("Sin estado")
            status_is_active = False
        status_key_value = (
            f"status:{effective_status_id}"
            if effective_status_id
            else f"status:{slugify(status_label) or 'sin-estado'}"
        )
        group_status = TaskDefinitionGroupValue(
            key=status_key_value,
            label=status_label,
        )

        category_label = (
            task.category.name
            if getattr(task, "category", None) and task.category.name
            else _("Sin categoría")
        )
        category_key_value = (
            f"category:{task.category_id}"
            if task.category_id
            else f"category:{slugify(category_label) or 'sin-categoria'}"
        )
        group_category = TaskDefinitionGroupValue(
            key=category_key_value,
            label=category_label,
        )

        mandatory_label = _("Obligatoria") if task.is_mandatory else _("Opcional")
        mandatory_badge_class = (
            "tm-badge tm-badge-critical" if task.is_mandatory else "tm-badge tm-badge-neutral"
        )
        mandatory_key_value = "mandatory:required" if task.is_mandatory else "mandatory:optional"
        group_mandatory = TaskDefinitionGroupValue(
            key=mandatory_key_value,
            label=mandatory_label,
        )

        criticality_value = task.criticality_level or TaskDefinition.CriticalityLevel.MEDIUM
        criticality_label = (
            task.get_criticality_level_display()
            if getattr(task, "criticality_level", None)
            else TaskDefinition.CriticalityLevel(criticality_value).label
        )
        criticality_badge_class_map = {
            TaskDefinition.CriticalityLevel.LOW: "tm-badge tm-badge-success",
            TaskDefinition.CriticalityLevel.MEDIUM: "tm-badge tm-badge-neutral",
            TaskDefinition.CriticalityLevel.HIGH: "tm-badge tm-badge-brand",
            TaskDefinition.CriticalityLevel.CRITICAL: "tm-badge tm-badge-critical",
        }
        criticality_badge_class = criticality_badge_class_map.get(
            criticality_value, "tm-badge tm-badge-neutral"
        )
        criticality_key_value = f"criticality:{criticality_value or 'unspecified'}"
        group_criticality = TaskDefinitionGroupValue(
            key=criticality_key_value,
            label=criticality_label,
        )

        scope_slug = slugify(scope_label) or scope_level or "general"
        scope_key_value = f"scope:{scope_level}:{scope_slug}"
        scope_subtitle = scope_badge_label if scope_badge_label and scope_badge_label != scope_label else None
        group_scope = TaskDefinitionGroupValue(
            key=scope_key_value,
            label=scope_label,
            subtitle=scope_subtitle,
        )

        if task.collaborator_id:
            responsible_key_value = f"responsible:collaborator:{task.collaborator_id}"
            responsible_label = responsible_main
            responsible_subtitle = responsible_secondary or None
        elif task.position_id:
            responsible_key_value = f"responsible:position:{task.position_id}"
            responsible_label = responsible_main
            responsible_subtitle = responsible_secondary or None
        else:
            responsible_key_value = "responsible:none"
            responsible_label = responsible_main or _("Sin responsable asignado")
            responsible_subtitle = responsible_secondary or None
        group_responsible = TaskDefinitionGroupValue(
            key=responsible_key_value,
            label=responsible_label,
            subtitle=responsible_subtitle,
        )

        task_type_value = task.task_type or "unspecified"
        task_type_key = f"task_type:{task_type_value}"
        group_task_type = TaskDefinitionGroupValue(
            key=task_type_key,
            label=task_type_label or _("Sin tipo definido"),
        )

        evidence_value = task.evidence_requirement or TaskDefinition.EvidenceRequirement.NONE
        evidence_key = f"evidence:{evidence_value}"
        evidence_group_label = evidence_label or _("Sin requisito")
        group_evidence = TaskDefinitionGroupValue(
            key=evidence_key,
            label=evidence_group_label,
        )

        rows.append(
            TaskDefinitionRow(
                id=task.pk,
                detail_url=reverse("task_manager:definition-detail", args=[task.pk]),
                update_url=reverse("task_manager:definition-update", args=[task.pk]),
                duplicate_url=reverse("task_manager:definition-duplicate", args=[task.pk]),
                name=task.name,
                description=task.description,
                category_label=category_label,
                status_label=status_label,
                status_is_active=status_is_active,
                is_mandatory=task.is_mandatory,
                mandatory_label=mandatory_label,
                mandatory_badge_class=mandatory_badge_class,
                criticality_level=criticality_value,
                criticality_label=criticality_label,
                criticality_badge_class=criticality_badge_class,
                task_type_label=task_type_label,
                schedule_summary=schedule_summary,
                schedule_segments=schedule_segments,
                schedule_detail=schedule_detail,
                is_one_time=is_one_time,
                scheduled_for_display=scheduled_for_display,
                scope_label=scope_label,
                scope_level=scope_level,
                scope_badge_label=scope_badge_label,
                responsible_main=responsible_main,
                responsible_secondary=responsible_secondary,
                evidence_label=evidence_label,
                has_evidence_requirement=task.evidence_requirement
                != TaskDefinition.EvidenceRequirement.NONE,
                record_label=record_label,
                requires_record_format=task.record_format != TaskDefinition.RecordFormat.NONE,
                created_on_display=date_format(task.created_at, "DATE_FORMAT"),
                display_order=task.display_order or 0,
                group_status=group_status,
                group_category=group_category,
                group_scope=group_scope,
                group_responsible=group_responsible,
                group_task_type=group_task_type,
                group_evidence=group_evidence,
                group_mandatory=group_mandatory,
                group_criticality=group_criticality,
            )
        )
    return rows


@dataclass(frozen=True)
class FilterOption:
    value: str
    label: str
    description: Optional[str] = None


@dataclass(frozen=True)
class FilterOptionGroup:
    key: str
    label: str
    options: Sequence[FilterOption]


@dataclass(frozen=True)
class FilterPickerData:
    default_value: str
    default_label: str
    groups: Sequence[FilterOptionGroup]
    search_enabled: bool = False
    neutral_value: Optional[str] = None


def build_scope_filter_groups() -> Sequence[FilterOptionGroup]:
    groups: list[FilterOptionGroup] = []

    generic_options = [
        FilterOption("all", _("Todos los lugares")),
        FilterOption("general", _("Cobertura general")),
        FilterOption("farm", _("Granjas (cualquier)")),
        FilterOption("house", _("Galpones (cualquier)")),
        FilterOption("room", _("Salones (cualquier)")),
    ]
    groups.append(
        FilterOptionGroup(
            key="generic",
            label=_("General"),
            options=generic_options,
        )
    )

    farms = Farm.objects.order_by("name").only("id", "name")
    if farms:
        farm_options = [
            FilterOption(
                value=f"farm:{farm.pk}",
                label=farm.name,
            )
            for farm in farms
            if farm.name
        ]
        if farm_options:
            groups.append(
                FilterOptionGroup(
                    key="farms",
                    label=_("Granjas específicas"),
                    options=farm_options,
                )
            )

    chicken_houses = (
        ChickenHouse.objects.select_related("farm")
        .order_by("farm__name", "name")
        .only("id", "name", "farm__name")
    )
    if chicken_houses:
        house_options = [
            FilterOption(
                value=f"house:{house.pk}",
                label=house.name,
                description=house.farm.name if house.farm else None,
            )
            for house in chicken_houses
            if house.name
        ]
        if house_options:
            groups.append(
                FilterOptionGroup(
                    key="houses",
                    label=_("Galpones"),
                    options=house_options,
                )
            )

    rooms = (
        Room.objects.select_related("chicken_house__farm")
        .order_by("chicken_house__farm__name", "chicken_house__name", "name")
        .only("id", "name", "chicken_house__name", "chicken_house__farm__name")
    )
    if rooms:
        room_options: list[FilterOption] = []
        for room in rooms:
            house = room.chicken_house
            farm = house.farm if house else None
            description_parts: list[str] = []
            if farm and getattr(farm, "name", ""):
                description_parts.append(farm.name)
            if house and getattr(house, "name", ""):
                description_parts.append(house.name)
            description = " · ".join(description_parts) if description_parts else None
            room_options.append(
                FilterOption(
                    value=f"room:{room.pk}",
                    label=room.name,
                    description=description,
                )
            )
        if room_options:
            groups.append(
                FilterOptionGroup(
                    key="rooms",
                    label=_("Salones"),
                    options=room_options,
                )
            )

    return groups


def build_responsible_filter_groups() -> Sequence[FilterOptionGroup]:
    groups: list[FilterOptionGroup] = []

    generic_options = [
        FilterOption("all", _("Todos")),
        FilterOption("collaborator", _("Con responsable")),
        FilterOption("position", _("Con posición")),
        FilterOption("unassigned", _("Sin responsable")),
    ]
    groups.append(
        FilterOptionGroup(
            key="generic",
            label=_("General"),
            options=generic_options,
        )
    )

    positions = (
        PositionDefinition.objects.select_related("farm", "chicken_house", "handoff_position")
        .order_by("display_order", "name")
        .only(
            "id",
            "name",
            "farm__name",
            "chicken_house__name",
            "handoff_position__id",
            "handoff_position__name",
            "handoff_position__code",
        )
    )
    if positions:
        position_options: list[FilterOption] = []
        for position in positions:
            description_parts: list[str] = []
            if position.farm and getattr(position.farm, "name", ""):
                description_parts.append(position.farm.name)
            if position.chicken_house and getattr(position.chicken_house, "name", ""):
                description_parts.append(position.chicken_house.name)
            description = " · ".join(description_parts) if description_parts else None
            position_options.append(
                FilterOption(
                    value=f"position:{position.pk}",
                    label=position.name,
                    description=description,
                )
            )
        if position_options:
            groups.append(
                FilterOptionGroup(
                    key="positions",
                    label=_("Posiciones específicas"),
                    options=position_options,
                )
            )

    collaborators = (
        UserProfile.objects.filter(is_active=True)
        .order_by("apellidos", "nombres")
        .only("id", "nombres", "apellidos", "cedula")
    )
    if collaborators:
        collaborator_options = [
            FilterOption(
                value=f"collaborator:{profile.pk}",
                label=profile.nombre_completo,
                description=profile.cedula or None,
            )
            for profile in collaborators
        ]
        if collaborator_options:
            groups.append(
                FilterOptionGroup(
                    key="collaborators",
                    label=_("Colaboradores"),
                    options=collaborator_options,
                )
            )

    return groups


def build_status_filter_groups(statuses: Iterable[TaskStatus]) -> Sequence[FilterOptionGroup]:
    groups = [
        FilterOptionGroup(
            key="generic",
            label=_("General"),
            options=[FilterOption("all", _("Todos los estados"))],
        )
    ]
    status_options = [
        FilterOption(str(status.pk), status.name)
        for status in statuses
        if status.name
    ]
    if status_options:
        groups.append(
            FilterOptionGroup(
                key="statuses",
                label=_("Estados disponibles"),
                options=status_options,
            )
        )
    return groups


def build_category_filter_groups(categories: Iterable[TaskCategory]) -> Sequence[FilterOptionGroup]:
    groups = [
        FilterOptionGroup(
            key="generic",
            label=_("General"),
            options=[FilterOption("all", _("Todas las categorías"))],
        )
    ]
    category_options = [
        FilterOption(str(category.pk), category.name)
        for category in categories
        if category.name
    ]
    if category_options:
        groups.append(
            FilterOptionGroup(
                key="categories",
                label=_("Categorías activas"),
                options=category_options,
            )
        )
    return groups


def build_mandatory_filter_groups() -> Sequence[FilterOptionGroup]:
    options = [
        FilterOption("all", _("Todas las tareas")),
        FilterOption("required", _("Solo obligatorias")),
        FilterOption("optional", _("Solo opcionales")),
    ]
    return [
        FilterOptionGroup(
            key="mandatory",
            label=_("Obligatoriedad"),
            options=options,
        )
    ]


def build_criticality_filter_groups() -> Sequence[FilterOptionGroup]:
    options: list[FilterOption] = [FilterOption("all", _("Cualquier nivel"))]
    for value, label in TaskDefinition.CriticalityLevel.choices:
        options.append(FilterOption(value, label))
    return [
        FilterOptionGroup(
            key="criticality",
            label=_("Nivel de criticidad"),
            options=options,
        )
    ]


def build_grouping_primary_filter_groups() -> Sequence[FilterOptionGroup]:
    options = [
        FilterOption("none", _("Sin agrupación")),
        FilterOption("status", _("Estado")),
        FilterOption("category", _("Categoría")),
        FilterOption("mandatory", _("Obligatoriedad")),
        FilterOption("criticality", _("Nivel de criticidad")),
        FilterOption("scope", _("Lugar")),
        FilterOption("responsible", _("Responsable sugerido")),
        FilterOption("task_type", _("Tipo de planificación")),
        FilterOption("evidence", _("Requisito de evidencia")),
    ]
    return [
        FilterOptionGroup(
            key="grouping-primary",
            label=_("Agrupar por"),
            options=options,
        )
    ]


def build_grouping_secondary_filter_groups() -> Sequence[FilterOptionGroup]:
    options = [
        FilterOption("none", _("No aplicar")),
        FilterOption("status", _("Estado")),
        FilterOption("category", _("Categoría")),
        FilterOption("mandatory", _("Obligatoriedad")),
        FilterOption("criticality", _("Nivel de criticidad")),
        FilterOption("scope", _("Lugar")),
        FilterOption("responsible", _("Responsable sugerido")),
        FilterOption("task_type", _("Tipo de planificación")),
        FilterOption("evidence", _("Requisito de evidencia")),
    ]
    return [
        FilterOptionGroup(
            key="grouping-secondary",
            label=_("Agrupación secundaria"),
            options=options,
        )
    ]


def build_assignment_farm_filter_groups() -> Sequence[FilterOptionGroup]:
    options = [
        FilterOption("all", _("Todas las granjas")),
        FilterOption("farm-el-guadual", "El Guadual"),
        FilterOption("farm-la-rivera", "La Rivera"),
        FilterOption("farm-altamira", "Altamira"),
    ]
    return [
        FilterOptionGroup(
            key="assignment-farms",
            label=_("Filtrar por granja"),
            options=options,
        )
    ]


def build_assignment_house_filter_groups() -> Sequence[FilterOptionGroup]:
    options = [
        FilterOption("all", _("Todos los galpones")),
        FilterOption("house-1", _("Galpón 1")),
        FilterOption("house-2", _("Galpón 2")),
        FilterOption("house-3", _("Galpón 3")),
    ]
    return [
        FilterOptionGroup(
            key="assignment-houses",
            label=_("Filtrar por galpón"),
            options=options,
        )
    ]


def build_assignment_state_filter_groups() -> Sequence[FilterOptionGroup]:
    options = [
        FilterOption("all", _("Todos los estados")),
        FilterOption("assigned", _("Asignadas")),
        FilterOption("unassigned", _("Sin responsable")),
        FilterOption("with_notes", _("Con observaciones")),
    ]
    return [
        FilterOptionGroup(
            key="assignment-state",
            label=_("Estado en calendario"),
            options=options,
        )
    ]


def build_followup_period_filter_groups() -> Sequence[FilterOptionGroup]:
    options = [
        FilterOption("week", _("Semana actual")),
        FilterOption("month", _("Mes actual")),
        FilterOption("quarter", _("Últimos 90 días")),
    ]
    return [
        FilterOptionGroup(
            key="followup-period",
            label=_("Rango temporal"),
            options=options,
        )
    ]


def build_today_view_filter_groups() -> Sequence[FilterOptionGroup]:
    options = [
        FilterOption("priority", _("Prioridad")),
        FilterOption("schedule", _("Horario")),
        FilterOption("status", _("Estado")),
    ]
    return [
        FilterOptionGroup(
            key="today-view",
            label=_("Orden de vista"),
            options=options,
        )
    ]


def locate_task_definition_page(
    queryset: QuerySet[TaskDefinition], task_id: int, per_page: int
) -> Optional[int]:
    if per_page <= 0:
        return None

    try:
        task = queryset.get(pk=task_id)
    except TaskDefinition.DoesNotExist:
        return None

    preceding = queryset.filter(
        Q(display_order__lt=task.display_order)
        | (
            Q(display_order=task.display_order)
            & (
                Q(name__lt=task.name)
                | (Q(name=task.name) & Q(pk__lt=task.pk))
            )
        )
    ).count()
    return preceding // per_page + 1


def build_compact_page_range(page_obj, edge_count: int = 1, around_count: int = 1) -> Sequence[Optional[int]]:
    paginator = page_obj.paginator
    total_pages = paginator.num_pages
    if total_pages <= 1:
        return [page_obj.number]

    current = page_obj.number
    included: set[int] = set()

    for num in range(1, edge_count + 1):
        included.add(num)
        included.add(total_pages - num + 1)

    for num in range(current - around_count, current + around_count + 1):
        if 1 <= num <= total_pages:
            included.add(num)

    sorted_pages = []
    last_page = 0
    for num in range(1, total_pages + 1):
        if num in included:
            if last_page and num - last_page > 1:
                sorted_pages.append(None)
            sorted_pages.append(num)
            last_page = num

    return sorted_pages


def format_task_schedule(task: TaskDefinition, task_type_label: str | None = None) -> tuple[str, Sequence[str]]:
    label = task_type_label or get_task_type_label(task)

    if not task.task_type:
        return label, [label]

    if task.task_type == TaskDefinition.TaskType.ONE_TIME:
        if task.scheduled_for:
            date_text = date_format(task.scheduled_for, "DATE_FORMAT")
        else:
            date_text = _("Sin fecha definida")
        summary = _("%(type)s · %(date)s") % {
            "type": label,
            "date": date_text,
        }
        return summary, [date_text]

    recurrence_fragments: list[str] = []

    if task.weekly_days:
        weekday_labels: list[str] = []
        for value in task.weekly_days:
            try:
                weekday_labels.append(str(DayOfWeek(value).label))
            except ValueError:
                weekday_labels.append(str(value))
        weekday_summary = ", ".join(weekday_labels)
        recurrence_fragments.append(_("Semanal: %(days)s") % {"days": weekday_summary})

    if task.fortnight_days:
        fortnight_labels = ", ".join(str(value) for value in task.fortnight_days)
        recurrence_fragments.append(_("Quincenal: días %(days)s") % {"days": fortnight_labels})

    if task.monthly_week_days:
        month_week_labels = ", ".join(str(value) for value in task.monthly_week_days)
        recurrence_fragments.append(_("Semanas del mes: %(weeks)s") % {"weeks": month_week_labels})

    if task.month_days:
        month_day_labels = ", ".join(str(value) for value in task.month_days)
        recurrence_fragments.append(_("Mensual: días %(days)s") % {"days": month_day_labels})

    recurrence_text = " · ".join(recurrence_fragments) if recurrence_fragments else _("Configuración pendiente")
    summary = _("%(type)s · %(recurrence)s") % {
        "type": label,
        "recurrence": recurrence_text,
    }
    segments = recurrence_fragments if recurrence_fragments else [recurrence_text]
    return summary, segments


def get_task_type_label(task: TaskDefinition) -> str:
    label = task.get_task_type_display()
    if not label:
        return _("Sin recurrencia")
    return label


def format_task_scope(task: TaskDefinition) -> tuple[str, str, str]:
    rooms = list(
        task.rooms.select_related("chicken_house__farm").all()
    )
    if not rooms and task.position_id:
        rooms = list(
            task.position.rooms.select_related("chicken_house__farm").all()
        )

    if rooms:
        label_list: list[str] = []
        for room in rooms:
            components: list[str] = []
            house = getattr(room, "chicken_house", None)
            farm = getattr(house, "farm", None) if house else None
            if farm and getattr(farm, "name", ""):
                components.append(farm.name)
            if house and getattr(house, "name", ""):
                components.append(house.name)
            if getattr(room, "name", ""):
                components.append(room.name)
            if not components:
                components.append(_("Sin ubicación"))
            label_list.append(" · ".join(components))
        labels = (label for label in label_list)
        return summarize_related_scope(labels, _("Salón")), "room", _("Salones")

    position = task.position
    if position and position.chicken_house:
        house = position.chicken_house
        farm = house.farm
        label = f"{farm.name} · {house.name}" if farm else house.name
        return label, "house", _("Galpones")

    if position and position.farm:
        return position.farm.name, "farm", _("Granjas")

    return _("Cobertura general"), "general", _("General")


def summarize_related_scope(labels: Iterable[str], singular_label: str) -> str:
    labels_list = [label for label in labels if label]
    if not labels_list:
        return singular_label

    first = labels_list[0]
    remaining = len(labels_list) - 1
    if remaining <= 0:
        return first

    return _("%(first)s +%(count)s más") % {"first": first, "count": remaining}


def format_task_responsible(task: TaskDefinition) -> tuple[str, str]:
    collaborator = task.collaborator.nombre_completo if task.collaborator else ""
    position = task.position.name if task.position else ""

    if collaborator and position:
        return collaborator, position

    if collaborator:
        return collaborator, ""

    if position:
        return position, ""

    return _("Sin responsable asignado"), ""

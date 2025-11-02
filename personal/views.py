from __future__ import annotations

import json
from collections import Counter, defaultdict
from itertools import cycle
from datetime import date, timedelta
from typing import Any, Dict, Iterable, List, Optional

from django import forms
from django.contrib import messages
from django.contrib.auth.views import LoginView, LogoutView
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError, Q
from django.http import HttpRequest, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views import View

from applacolina.mixins import StaffRequiredMixin

from .forms import (
    AssignmentCreateForm,
    AssignmentUpdateForm,
    CalendarGenerationForm,
    OperatorRestPeriodForm,
    OperatorProfileForm,
    PortalAuthenticationForm,
    PositionDefinitionForm,
)
from .models import (
    AssignmentAlertLevel,
    AssignmentChangeLog,
    DayOfWeek,
    CalendarStatus,
    OperatorRestPeriod,
    PositionCategory,
    PositionCategoryCode,
    PositionDefinition,
    RestPeriodSource,
    RestPeriodStatus,
    Role,
    ShiftAssignment,
    ShiftCalendar,
    ShiftType,
    UserProfile,
    resolve_overload_policy,
)
from .services import CalendarScheduler, SchedulerOptions, sync_calendar_rest_periods
from production.models import ChickenHouse, Farm, Room


class CalendarPortalView(LoginView):
    template_name = "users/login.html"
    form_class = PortalAuthenticationForm
    redirect_authenticated_user = True

    def get_success_url(self):
        redirect_to = self.get_redirect_url()
        if redirect_to:
            return redirect_to
        user = getattr(self.request, "user", None)
        if user and getattr(user, "is_staff", False):
            return reverse("personal:dashboard")
        return reverse("task_manager:telegram-mini-app")


class CalendarLogoutView(StaffRequiredMixin, LogoutView):
    # Explicitly allow GET requests; Django 5 restricts logout to POST by default.
    http_method_names = ["get", "head", "options", "post"]
    next_page = reverse_lazy("portal:login")

    def get(self, request, *args, **kwargs):
        """Allow GET requests to trigger the logout flow."""
        return self.post(request, *args, **kwargs)


def _parse_date(value: str, field_name: str) -> date:
    parsed = parse_date(value)
    if not parsed:
        raise ValueError(f"Formato de fecha inválido para {field_name}.")
    return parsed


def _date_range(start: date, end: date) -> List[date]:
    days = (end - start).days
    return [start + timedelta(days=offset) for offset in range(days + 1)]


def _resolve_calendar_home_url(*, exclude_ids: Iterable[int] | None = None) -> str:
    queryset = ShiftCalendar.objects.order_by("-start_date", "-created_at")
    if exclude_ids:
        queryset = queryset.exclude(pk__in=list(exclude_ids))

    next_calendar = queryset.first()
    if next_calendar:
        return reverse("personal:calendar-detail", args=[next_calendar.pk])

    return reverse("personal:configurator")


def _recent_calendars_payload(*, limit: int = 3, exclude_ids: Iterable[int] | None = None) -> list[dict[str, Any]]:
    queryset = ShiftCalendar.objects.order_by("-start_date", "-created_at")
    if exclude_ids:
        queryset = queryset.exclude(pk__in=list(exclude_ids))
    recent_calendars = list(queryset[:limit])

    return [
        {
            "id": calendar.id,
            "display_name": calendar.name or f"Calendario {calendar.start_date:%d/%m/%Y}",
            "start_date": calendar.start_date,
            "end_date": calendar.end_date,
            "status": calendar.status,
            "status_label": calendar.get_status_display(),
        }
        for calendar in recent_calendars
    ]


REST_CELL_STATE_REST = "rest"
REST_CELL_STATE_UNASSIGNED = "unassigned"
REST_CELL_STATE_ASSIGNED = "assigned"
REST_CELL_STATE_INACTIVE = "inactive"


def _operator_display_name(operator: Optional[UserProfile]) -> str:
    if not operator:
        return ""
    return operator.get_full_name() or operator.nombres or operator.apellidos or ""


def _primary_role_label(operator: Optional[UserProfile]) -> str:
    if not operator:
        return ""
    roles = list(operator.roles.all())
    return roles[0].get_name_display() if roles else ""


def _build_rest_rows(
    date_columns: Iterable[date],
    assignments_by_operator_day: dict[tuple[int, date], List[ShiftAssignment]],
    rest_periods: Iterable[OperatorRestPeriod],
    operator_map: dict[int, UserProfile],
    operator_ids_with_assignments: set[int],
) -> tuple[list[dict[str, Any]], set[int]]:
    date_list = list(date_columns)
    if not date_list:
        return [], set()

    rest_periods_by_operator: dict[int, List[OperatorRestPeriod]] = defaultdict(list)
    for rest_period in rest_periods:
        operator_id = getattr(rest_period.operator, "id", None)
        if not operator_id:
            continue
        rest_periods_by_operator[operator_id].append(rest_period)

    for periods in rest_periods_by_operator.values():
        periods.sort(key=lambda period: (period.start_date, period.end_date))

    rest_operator_ids = set(rest_periods_by_operator.keys())
    operator_ids = operator_ids_with_assignments | rest_operator_ids
    if not operator_ids:
        return [], rest_operator_ids

    def _sort_key(operator_id: int) -> tuple[str, str, int]:
        operator = operator_map.get(operator_id)
        if not operator:
            return ("", "", operator_id)
        return (
            (operator.apellidos or "").lower(),
            (operator.nombres or "").lower(),
            operator.id or 0,
        )

    ordered_operator_ids = sorted(operator_ids, key=_sort_key)
    first_day = date_list[0]
    last_day = date_list[-1]

    rest_rows: list[dict[str, Any]] = []

    for operator_id in ordered_operator_ids:
        operator = operator_map.get(operator_id)
        if not operator:
            continue
        if operator.is_staff:
            continue

        operator_name = _operator_display_name(operator)
        role_label = _primary_role_label(operator)

        rest_by_day: dict[date, OperatorRestPeriod] = {}
        for period in rest_periods_by_operator.get(operator_id, []):
            start = max(period.start_date, first_day)
            end = min(period.end_date, last_day)
            if start > end:
                continue
            current = start
            while current <= end:
                rest_by_day[current] = period
                current += timedelta(days=1)

        cells: list[dict[str, Any]] = []
        has_rest_day = False
        has_unassigned_day = False

        for day in date_list:
            period = rest_by_day.get(day)
            assignments = assignments_by_operator_day.get((operator_id, day), [])
            is_assigned = bool(assignments)
            is_active = operator.is_active_on(day)
            date_iso = day.isoformat()
            date_display = day.strftime("%d/%m/%Y")

            if period:
                has_rest_day = True
                reason = period.notes or period.get_status_display()
                status_label = period.get_status_display()
                cells.append(
                    {
                        "operator_id": operator_id,
                        "name": operator_name,
                        "role": role_label,
                        "reason": reason,
                        "state": REST_CELL_STATE_REST,
                        "status_label": status_label,
                        "date": date_iso,
                        "date_display": date_display,
                        "rest_period_id": period.id,
                        "rest_source": period.source or "",
                    }
                )
                continue

            if is_assigned:
                assignment_count = len(assignments)
                cells.append(
                    {
                        "operator_id": operator_id,
                        "name": operator_name,
                        "role": role_label,
                        "reason": "",
                        "state": REST_CELL_STATE_ASSIGNED,
                        "status_label": "Turno asignado",
                        "date": date_iso,
                        "date_display": date_display,
                        "rest_period_id": None,
                        "rest_source": "",
                        "assignment_count": assignment_count,
                    }
                )
                continue

            if is_active:
                has_unassigned_day = True
                cells.append(
                    {
                        "operator_id": operator_id,
                        "name": operator_name,
                        "role": role_label,
                        "reason": "",
                        "state": REST_CELL_STATE_UNASSIGNED,
                        "status_label": "Sin asignación",
                        "date": date_iso,
                        "date_display": date_display,
                        "rest_period_id": None,
                        "rest_source": "",
                    }
                )
                continue

            cells.append(
                {
                    "operator_id": operator_id,
                    "name": operator_name,
                    "role": role_label,
                    "reason": "",
                    "state": REST_CELL_STATE_INACTIVE,
                    "status_label": "",
                    "date": date_iso,
                    "date_display": date_display,
                    "rest_period_id": None,
                    "rest_source": "",
                }
            )

        if has_rest_day or has_unassigned_day:
            rest_rows.append(
                {
                    "label": role_label or "Colaborador",
                    "slot": operator_name or f"Operador #{operator_id}",
                    "operator_id": operator_id,
                    "has_rest": has_rest_day,
                    "has_unassigned": has_unassigned_day,
                    "cells": cells,
                }
            )

    return rest_rows, rest_operator_ids


def _build_assignment_matrix(
    calendar: ShiftCalendar,
) -> tuple[
    List[date],
    List[dict[str, Any]],
    List[dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, Any],
]:
    date_columns = _date_range(calendar.start_date, calendar.end_date)
    if not date_columns:
        empty_rows, empty_rest_rows, position_groups = _build_operational_position_groups([], [])
        return [], empty_rows, empty_rest_rows, {}, position_groups

    assignments = list(
        calendar.assignments.select_related(
            "position",
            "position__category",
            "position__farm",
            "operator",
        )
        .prefetch_related("operator__roles", "operator__suggested_positions")
        .order_by("position__display_order", "position__code", "date")
    )

    assignment_map: dict[tuple[int, date], ShiftAssignment] = {}
    operator_map: dict[int, UserProfile] = {}
    assignments_by_operator_day: dict[tuple[int, date], List[ShiftAssignment]] = defaultdict(list)
    position_ids_with_assignments: set[int] = set()
    operator_ids_with_assignments: set[int] = set()
    suggestion_map: dict[int, set[int]] = {}

    for assignment in assignments:
        assignment_map[(assignment.position_id, assignment.date)] = assignment
        position_ids_with_assignments.add(assignment.position_id)
        if assignment.operator_id:
            assignments_by_operator_day[(assignment.operator_id, assignment.date)].append(assignment)
            operator_ids_with_assignments.add(assignment.operator_id)
            operator = assignment.operator
            if operator and assignment.operator_id not in suggestion_map:
                suggestion_map[assignment.operator_id] = {
                    position.id for position in operator.suggested_positions.all()
                }
            if operator and assignment.operator_id not in operator_map:
                operator_map[assignment.operator_id] = operator

    assigned_operator_ids_by_day: dict[date, set[int]] = defaultdict(set)
    for (operator_id, assignment_day), related_assignments in assignments_by_operator_day.items():
        if related_assignments:
            assigned_operator_ids_by_day[assignment_day].add(operator_id)

    active_position_filter = (
        Q(valid_from__lte=calendar.end_date)
        & (Q(valid_until__isnull=True) | Q(valid_until__gte=calendar.start_date))
    )

    positions = list(
        PositionDefinition.objects.select_related("category", "farm")
        .prefetch_related("rooms")
        .filter(active_position_filter | Q(id__in=position_ids_with_assignments))
        .order_by("display_order", "code")
        .distinct()
    )

    choices_map = _eligible_operator_map(calendar, positions, date_columns, assignments)

    farm_order_index: dict[int, int] = {}
    farm_candidates: list[tuple[str, int]] = []
    for position in positions:
        farm = getattr(position, "farm", None)
        if farm and farm.id is not None and farm.id not in farm_order_index:
            farm_candidates.append((farm.name or "", farm.id))
    farm_candidates.sort(key=lambda item: (item[0].lower(), item[1]))
    for idx, (_, farm_id) in enumerate(farm_candidates):
        farm_order_index[farm_id] = idx
    default_farm_order = len(farm_order_index)

    alert_priority = {
        AssignmentAlertLevel.NONE: 0,
        AssignmentAlertLevel.WARN: 1,
        AssignmentAlertLevel.CRITICAL: 2,
    }

    def _normalize_alert_level(
        value: AssignmentAlertLevel | str | None,
    ) -> AssignmentAlertLevel:
        if isinstance(value, AssignmentAlertLevel):
            return value
        if value:
            try:
                return AssignmentAlertLevel(value)
            except ValueError:
                return AssignmentAlertLevel.NONE
        return AssignmentAlertLevel.NONE

    def _escalate_alert(
        current: AssignmentAlertLevel,
        candidate: AssignmentAlertLevel,
    ) -> AssignmentAlertLevel:
        if alert_priority[candidate] > alert_priority[current]:
            return candidate
        return current

    rest_periods = list(
        OperatorRestPeriod.objects.select_related("operator")
        .prefetch_related("operator__roles")
        .filter(start_date__lte=calendar.end_date, end_date__gte=calendar.start_date)
        .order_by("start_date", "operator__apellidos", "operator__nombres")
    )

    resting_operator_ids_by_day: dict[date, set[int]] = defaultdict(set)
    for rest_period in rest_periods:
        operator = rest_period.operator
        operator_id = getattr(operator, "id", None)
        if not operator_id:
            continue
        if operator and operator.is_staff:
            continue
        if operator_id not in operator_map and operator:
            operator_map[operator_id] = operator
        start = max(rest_period.start_date, calendar.start_date)
        end = min(rest_period.end_date, calendar.end_date)
        current_day = start
        while current_day <= end:
            resting_operator_ids_by_day[current_day].add(operator_id)
            current_day += timedelta(days=1)

    available_operator_qs = (
        UserProfile.objects.filter(
            Q(employment_start_date__isnull=True) | Q(employment_start_date__lte=calendar.end_date),
            Q(employment_end_date__isnull=True) | Q(employment_end_date__gte=calendar.start_date),
            is_active=True,
            is_staff=False,
        )
        .prefetch_related("roles")
        .order_by("apellidos", "nombres")
    )

    active_candidates_by_day: dict[date, set[int]] = defaultdict(set)
    for operator in available_operator_qs:
        if operator.id not in operator_map:
            operator_map[operator.id] = operator
        for target_day in date_columns:
            if operator.is_active_on(target_day):
                active_candidates_by_day[target_day].add(operator.id)

    def _suggestion_gap_message(
        assignment: ShiftAssignment,
        position: PositionDefinition,
    ) -> Optional[str]:
        if not assignment or not assignment.operator_id:
            return None

        suggested_positions = suggestion_map.get(assignment.operator_id)
        if suggested_positions is None:
            operator = assignment.operator
            if operator:
                suggested_positions = {
                    pos.id for pos in operator.suggested_positions.all()
                }
            else:  # pragma: no cover - defensive branch
                suggested_positions = set()
            suggestion_map[assignment.operator_id] = suggested_positions

        if position.id not in suggested_positions:
            if assignment.is_auto_assigned:
                return "Operario sin sugerencia registrada. Ajusta programación."
            return "Operario sin sugerencia registrada. Asignación manual confirmada."

        return None

    def _overtime_message(assignment: ShiftAssignment) -> Optional[str]:
        if not assignment.operator_id or not assignment.is_overtime:
            return None

        related = assignments_by_operator_day.get((assignment.operator_id, assignment.date), [])
        if len(related) > 1:
            return "Operario con doble turno hoy. Ajusta descansos."
        return "Operario con sobrecarga consecutiva. Ajusta descansos."

    def _operator_sort_key(operator_id: int) -> tuple[str, str, int]:
        operator = operator_map.get(operator_id)
        if not operator:
            return ("", "", operator_id)
        return (
            (operator.apellidos or "").lower(),
            (operator.nombres or "").lower(),
            operator_id,
        )

    def _operator_status_suffix(operator_id: int, target_day: date) -> str:
        if operator_id in resting_operator_ids_by_day.get(target_day, set()):
            return "En descanso"

        assignments_today = assignments_by_operator_day.get((operator_id, target_day), [])
        if assignments_today:
            position_labels: list[str] = []
            seen_labels: set[str] = set()
            for related_assignment in assignments_today:
                position = getattr(related_assignment, "position", None)
                if position and position.name:
                    label = position.name
                elif position and position.code:
                    label = position.code
                elif related_assignment.position_id:
                    label = f"#{related_assignment.position_id}"
                else:
                    label = "Asignado"
                if label not in seen_labels:
                    seen_labels.add(label)
                    position_labels.append(label)
            if position_labels:
                joined = ", ".join(position_labels)
                return f"{joined}"
            return "Posición asignada"

        return "Disponible"

    rows: List[dict[str, Any]] = []
    for position in positions:
        position_choices = choices_map.get(position.id, {})
        row_cells: List[dict[str, Any]] = []
        for day in date_columns:
            assignment = assignment_map.get((position.id, day))
            is_active = position.is_active_on(day)

            raw_choices = position_choices.get(day, [])
            choices = [dict(option) for option in raw_choices]
            choices.sort(key=lambda item: item["label"].lower())

            if assignment and assignment.operator_id:
                operator = getattr(assignment, "operator", None)
                if operator and not any(option["id"] == assignment.operator_id for option in choices):
                    choices.insert(
                        0,
                        {
                            "id": assignment.operator_id,
                            "label": _format_operator_label(operator),
                            "alert": AssignmentAlertLevel.NONE,
                            "disabled": False,
                        },
                    )
                elif operator is None and not any(option["id"] == assignment.operator_id for option in choices):
                    choices.insert(
                        0,
                        {
                            "id": assignment.operator_id,
                            "label": str(assignment.operator_id),
                            "alert": AssignmentAlertLevel.NONE,
                            "disabled": False,
                        },
                    )

            if not is_active:
                choices = []

            skill_gap_message = _suggestion_gap_message(assignment, position) if assignment else None
            is_overtime = bool(assignment and assignment.is_overtime)
            overtime_message = _overtime_message(assignment) if assignment else None

            alert_level = _normalize_alert_level(assignment.alert_level if assignment else None)

            if not assignment:
                alert_level = (
                    AssignmentAlertLevel.CRITICAL if is_active else AssignmentAlertLevel.NONE
                )
            else:
                if is_overtime:
                    alert_level = _escalate_alert(alert_level, AssignmentAlertLevel.CRITICAL)
                if skill_gap_message:
                    manual_override = not assignment.is_auto_assigned
                    desired_alert = (
                        AssignmentAlertLevel.WARN if manual_override else AssignmentAlertLevel.CRITICAL
                    )
                    alert_level = _escalate_alert(alert_level, desired_alert)

            if is_active:
                choice_lookup: dict[int, dict[str, Any]] = {
                    option_id: option
                    for option in choices
                    if (option_id := option.get("id")) not in (None, "")
                }
                operator_ids: set[int] = set(choice_lookup.keys())
                operator_ids.update(active_candidates_by_day.get(day, set()))
                operator_ids.update(resting_operator_ids_by_day.get(day, set()))
                operator_ids.update(assigned_operator_ids_by_day.get(day, set()))
                if assignment and assignment.operator_id:
                    operator_ids.add(assignment.operator_id)

                sorted_choices: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
                assigned_today_ids = assigned_operator_ids_by_day.get(day, set())

                for operator_id in sorted(operator_ids, key=_operator_sort_key):
                    if not operator_id:
                        continue

                    operator = operator_map.get(operator_id)
                    if not operator and assignment and assignment.operator_id == operator_id:
                        operator = assignment.operator
                        if operator:
                            operator_map[operator_id] = operator
                    if not operator:
                        continue

                    is_operator_active = operator.is_active_on(day)
                    if not is_operator_active:
                        is_assigned_today = operator_id in assigned_today_ids
                        if assignment and assignment.operator_id == operator_id:
                            is_assigned_today = True
                        if not is_assigned_today:
                            continue

                    base_choice = choice_lookup.get(operator_id)
                    choice_data = dict(base_choice) if base_choice else {
                        "id": operator_id,
                        "label": "",
                        "alert": AssignmentAlertLevel.NONE,
                        "disabled": False,
                    }

                    is_current_assignment_operator = bool(assignment and assignment.operator_id == operator_id)
                    if operator.is_staff and not is_current_assignment_operator:
                        continue

                    base_label = _format_operator_label(operator)
                    suffix = _operator_status_suffix(operator_id, day)
                    choice_data["label"] = f"{base_label} - {suffix}" if suffix else base_label
                    choice_data["disabled"] = False

                    assignments_today = assignments_by_operator_day.get((operator_id, day), [])
                    is_assigned_today = bool(assignments_today)
                    is_resting_today = operator_id in resting_operator_ids_by_day.get(day, set())

                    if is_assigned_today:
                        farm_orders: list[int] = []
                        for related_assignment in assignments_today:
                            related_position = getattr(related_assignment, "position", None)
                            if related_position and related_position.farm_id is not None:
                                farm_orders.append(
                                    farm_order_index.get(related_position.farm_id, default_farm_order)
                                )
                        primary_farm_order = min(farm_orders) if farm_orders else default_farm_order
                        status_priority = 1
                    elif is_resting_today:
                        primary_farm_order = default_farm_order + 1
                        status_priority = 2
                    else:
                        primary_farm_order = -1
                        status_priority = 0

                    operator_sort = _operator_sort_key(operator_id)
                    sort_key = (
                        status_priority,
                        primary_farm_order,
                        operator_sort[0],
                        operator_sort[1],
                        operator_sort[2],
                    )
                    sorted_choices.append((sort_key, choice_data))

                sorted_choices.sort(key=lambda item: item[0])
                choices = [choice for _, choice in sorted_choices]

                if assignment:
                    choices.insert(
                        0,
                        {
                            "id": "",
                            "label": "Selecciona",
                            "alert": AssignmentAlertLevel.NONE,
                            "disabled": False,
                        },
                    )

            row_cells.append(
                {
                    "date": day,
                    "assignment": assignment,
                    "alert": alert_level.value,
                    "is_position_active": is_active,
                    "choices": choices,
                    "skill_gap_message": skill_gap_message,
                    "is_overtime": is_overtime,
                    "overtime_message": overtime_message,
                }
            )
        rows.append(
            {
                "position": position,
                "cells": row_cells,
            }
        )

    rest_rows, rest_operator_ids = _build_rest_rows(
        date_columns=date_columns,
        assignments_by_operator_day=assignments_by_operator_day,
        rest_periods=rest_periods,
        operator_map=operator_map,
        operator_ids_with_assignments=operator_ids_with_assignments,
    )

    relevant_operator_ids = operator_ids_with_assignments | rest_operator_ids

    rest_summary: dict[str, dict[str, Any]] = {}
    if relevant_operator_ids:
        periods_for_summary = list(
            OperatorRestPeriod.objects.filter(operator_id__in=relevant_operator_ids)
            .order_by("start_date", "end_date")
            .select_related("operator")
        )
        periods_by_operator: dict[int, List[OperatorRestPeriod]] = defaultdict(list)
        for period in periods_for_summary:
            periods_by_operator[period.operator_id].append(period)

        def _period_payload(period: OperatorRestPeriod) -> dict[str, Any]:
            return {
                "id": period.id,
                "start": period.start_date.isoformat(),
                "end": period.end_date.isoformat(),
                "status": period.status,
                "status_label": period.get_status_display(),
                "source": period.source,
                "source_label": period.get_source_display(),
                "notes": period.notes,
            }

        for operator_id in relevant_operator_ids:
            operator = operator_map.get(operator_id)
            if not operator:
                continue
            if operator.is_staff:
                continue

            periods = periods_by_operator.get(operator_id, [])
            current_period = next(
                (
                    period
                    for period in periods
                    if period.start_date <= calendar.end_date and period.end_date >= calendar.start_date
                ),
                None,
            )
            recent_period = None
            upcoming_period = None

            for period in periods:
                if period.end_date < calendar.start_date:
                    if not recent_period or recent_period.end_date < period.end_date:
                        recent_period = period
                elif period.start_date > calendar.end_date:
                    if not upcoming_period or upcoming_period.start_date > period.start_date:
                        upcoming_period = period

            rest_summary[str(operator_id)] = {
                "id": operator_id,
                "name": operator.get_full_name() or operator.nombres or operator.apellidos or "",
                "employment_start": (
                    operator.employment_start_date.isoformat()
                    if operator.employment_start_date
                    else None
                ),
                "employment_end": (
                    operator.employment_end_date.isoformat()
                    if operator.employment_end_date
                    else None
                ),
                "current": _period_payload(current_period) if current_period else None,
                "recent": _period_payload(recent_period) if recent_period else None,
                "upcoming": _period_payload(upcoming_period) if upcoming_period else None,
            }

    rows, rest_rows, position_groups = _build_operational_position_groups(rows, rest_rows)

    return date_columns, rows, rest_rows, rest_summary, position_groups


def _eligible_operator_map(
    calendar: ShiftCalendar,
    positions: Iterable[PositionDefinition],
    date_columns: Iterable[date],
    assignment_list: Iterable[Any],
) -> dict[int, dict[date, List[dict[str, Any]]]]:
    positions = list(positions)
    date_columns = list(date_columns)

    if not positions or not date_columns:
        return {}

    position_ids = {position.id for position in positions if position.id}
    if not position_ids:
        return {}

    assignment_by_operator_day: dict[tuple[int, date], List[Any]] = defaultdict(list)
    for assignment in assignment_list:
        if assignment.operator_id:
            assignment_by_operator_day[(assignment.operator_id, assignment.date)].append(
                assignment
            )

    operator_qs = (
        UserProfile.objects.prefetch_related("roles", "suggested_positions")
        .filter(suggested_positions__in=position_ids, is_staff=False)
        .distinct()
    )

    operators_by_position: dict[int, set[int]] = defaultdict(set)
    operator_cache: dict[int, Any] = {}

    for operator in operator_qs:
        operator_cache[operator.id] = operator
        for suggested in operator.suggested_positions.all():
            if suggested.id in position_ids:
                operators_by_position[suggested.id].add(operator.id)

    result: dict[int, dict[date, List[dict[str, Any]]]] = defaultdict(dict)

    for position in positions:
        operator_ids = operators_by_position.get(position.id, set())
        for day in date_columns:
            if not position.is_active_on(day):
                result[position.id][day] = []
                continue

            if not operator_ids:
                result[position.id][day] = []
                continue

            choices: List[dict[str, Any]] = []
            for operator_id in operator_ids:
                operator = operator_cache.get(operator_id)
                if not operator:
                    continue

                if not operator.is_active_on(day):
                    continue

                busy_assignments = assignment_by_operator_day.get((operator_id, day), [])
                disabled = any(assign.position_id != position.id for assign in busy_assignments)

                roles = list(operator.roles.all())
                role_label = roles[0].get_name_display() if roles else ""
                label = operator.get_full_name() or operator.nombres
                if role_label:
                    label = f"{label} · {role_label}"

                choices.append(
                    {
                        "id": operator_id,
                        "label": label,
                        "alert": AssignmentAlertLevel.NONE,
                        "disabled": disabled,
                    }
                )

            choices.sort(key=lambda item: item["label"].lower())
            result[position.id][day] = choices

    return result


def _calculate_stats(rows: Iterable[dict[str, Any]]) -> dict[str, int]:
    stats = {
        "total_assignments": 0,
        "gaps": 0,
        "warn": 0,
        "critical": 0,
        "overtime": 0,
    }
    for row in rows:
        for cell in row["cells"]:
            assignment = cell["assignment"]
            alert_value = cell.get("alert") or AssignmentAlertLevel.NONE.value
            try:
                alert_level = AssignmentAlertLevel(alert_value)
            except ValueError:  # pragma: no cover - defensive
                alert_level = AssignmentAlertLevel.NONE
            if not assignment:
                stats["gaps"] += 1
                if alert_level == AssignmentAlertLevel.CRITICAL:
                    stats["critical"] += 1
                continue

            stats["total_assignments"] += 1
            if alert_level == AssignmentAlertLevel.WARN:
                stats["warn"] += 1
            if alert_level == AssignmentAlertLevel.CRITICAL:
                stats["critical"] += 1
            if cell.get("is_overtime"):
                stats["overtime"] += 1

    return stats


ISSUE_STYLE_MAP: dict[str, dict[str, str]] = {
    "critical": {
        "indicator_class": "bg-red-500",
        "badge_class": "border border-red-200 bg-red-50 text-red-700",
        "label": "Crítico",
    },
    "warning": {
        "indicator_class": "bg-amber-400",
        "badge_class": "border border-amber-200 bg-amber-50 text-amber-700",
        "label": "Advertencia",
    },
    "info": {
        "indicator_class": "bg-sky-400",
        "badge_class": "border border-sky-200 bg-sky-50 text-sky-700",
        "label": "Seguimiento",
    },
}


def _identify_calendar_issues(
    date_columns: Iterable[date],
    rows: Iterable[dict[str, Any]],
    rest_rows: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    calendar_days = list(date_columns)
    if not calendar_days:
        return []

    operator_metrics: dict[int, dict[str, Any]] = {}

    def _ensure_entry(operator_id: int) -> dict[str, Any]:
        entry = operator_metrics.get(operator_id)
        if entry is None:
            entry = {
                "name": "",
                "work_days": {},  # date -> count of assignments
                "rest_days": set(),
                "unassigned_days": set(),
                "categories": {},
                "overtime_days": set(),
                "skill_gaps": [],
            }
            operator_metrics[operator_id] = entry
        return entry

    for row in rows:
        position: Optional[PositionDefinition] = row.get("position")
        category = getattr(position, "category", None)
        category_payload: Optional[dict[str, Any]] = None
        if category and category.id is not None:
            category_payload = {
                "id": category.id,
                "name": category.display_name,
                "rest_max": category.rest_max_consecutive_days,
                "rest_monthly": category.rest_monthly_days,
                "rest_post": category.rest_post_shift_days,
            }

        for cell in row.get("cells", []):
            assignment: Optional[ShiftAssignment] = cell.get("assignment")
            if not assignment or not assignment.operator_id:
                continue

            operator_id = assignment.operator_id
            entry = _ensure_entry(operator_id)
            operator = getattr(assignment, "operator", None)
            if not entry["name"]:
                entry["name"] = _operator_display_name(operator) or f"Operador #{operator_id}"

            day: date = cell.get("date")
            if not isinstance(day, date):
                continue

            current_count = entry["work_days"].get(day, 0)
            entry["work_days"][day] = current_count + 1

            if cell.get("is_overtime") or getattr(assignment, "is_overtime", False):
                entry["overtime_days"].add(day)

            skill_gap_message = cell.get("skill_gap_message")
            if skill_gap_message:
                entry["skill_gaps"].append(
                    {
                        "message": skill_gap_message,
                        "position": position.name if position and position.name else getattr(position, "code", ""),
                        "date": day,
                        "is_manual": not assignment.is_auto_assigned,
                    }
                )

            if category_payload:
                entry["categories"].setdefault(category_payload["id"], category_payload)

    for rest_row in rest_rows:
        operator_id = rest_row.get("operator_id")
        for cell in rest_row.get("cells", []):
            cell_operator_id = cell.get("operator_id") or operator_id
            if not cell_operator_id:
                continue

            entry = _ensure_entry(cell_operator_id)
            if not entry["name"]:
                entry["name"] = cell.get("name") or rest_row.get("slot") or f"Operador #{cell_operator_id}"

            raw_date = cell.get("date")
            if isinstance(raw_date, date):
                cell_date = raw_date
            elif isinstance(raw_date, str):
                try:
                    cell_date = date.fromisoformat(raw_date)
                except ValueError:
                    continue
            else:
                continue

            state = cell.get("state")
            if state == REST_CELL_STATE_REST:
                entry["rest_days"].add(cell_date)
            elif state == REST_CELL_STATE_ASSIGNED:
                entry["work_days"][cell_date] = max(entry["work_days"].get(cell_date, 0), cell.get("assignment_count", 1))
            elif state == REST_CELL_STATE_UNASSIGNED:
                entry["unassigned_days"].add(cell_date)

    severity_weight = {"critical": 3, "warning": 2, "info": 1}
    issues: list[dict[str, Any]] = []

    for operator_id, data in operator_metrics.items():
        name = data.get("name") or f"Operador #{operator_id}"
        work_days: dict[date, int] = data.get("work_days", {})
        if not work_days:
            continue

        categories = list(data.get("categories", {}).values())
        primary_category = None
        if categories:
            primary_category = min(
                categories,
                key=lambda item: (
                    item.get("rest_max") if item.get("rest_max") is not None else float("inf"),
                    item.get("rest_monthly") if item.get("rest_monthly") is not None else float("inf"),
                ),
            )

        rest_max_limit = primary_category.get("rest_max") if primary_category else None
        rest_monthly_limit = primary_category.get("rest_monthly") if primary_category else None
        category_name = primary_category.get("name") if primary_category else None

        work_day_set = set(work_days.keys())

        max_streak = 0
        max_streak_end: Optional[date] = None
        current_streak = 0

        for day in calendar_days:
            if day in work_day_set:
                current_streak += 1
                if current_streak > max_streak:
                    max_streak = current_streak
                    max_streak_end = day
            else:
                current_streak = 0

        if max_streak_end and max_streak:
            streak_start = max_streak_end - timedelta(days=max_streak - 1)
            limit_label = f"{rest_max_limit} días" if rest_max_limit else "sin dato de límite"
            if rest_max_limit and max_streak > rest_max_limit:
                issues.append(
                    {
                        "severity": "critical",
                        "title": f"{name} acumula {max_streak} días consecutivos de turno",
                        "detail": (
                            f"Periodo {streak_start:%d/%m} → {max_streak_end:%d/%m}. "
                            f"Límite de {category_name or 'categoría desconocida'}: {limit_label}."
                        ),
                        "score": max_streak - rest_max_limit,
                    }
                )
            elif rest_max_limit and max_streak == rest_max_limit and max_streak >= 2:
                issues.append(
                    {
                        "severity": "warning",
                        "title": f"{name} está al límite de días consecutivos permitidos",
                        "detail": (
                            f"{max_streak} días consecutivos del {streak_start:%d/%m} al {max_streak_end:%d/%m}. "
                            f"Verifica descansos para {category_name or 'la categoría'}."
                        ),
                        "score": max_streak,
                    }
                )
            elif not rest_max_limit and max_streak >= 6:
                issues.append(
                    {
                        "severity": "info",
                        "title": f"{name} suma {max_streak} días seguidos de turno",
                        "detail": (
                            f"Sin dato de límite configurado para evaluar descansos. "
                            f"Periodo {streak_start:%d/%m} → {max_streak_end:%d/%m}."
                        ),
                        "score": max_streak,
                    }
                )

        rest_days: set[date] = data.get("rest_days", set())
        unassigned_days: set[date] = data.get("unassigned_days", set())

        expected_rest_span = None
        if primary_category:
            rest_post_config = primary_category.get("rest_post")
            if rest_post_config is not None:
                expected_rest_span = max(1, int(rest_post_config) or 1)
        if expected_rest_span is None:
            expected_rest_span = 1

        if rest_days:
            max_rest_streak = 0
            max_rest_end: Optional[date] = None
            current_rest_streak = 0
            for day in calendar_days:
                if day in rest_days:
                    current_rest_streak += 1
                    if current_rest_streak > max_rest_streak:
                        max_rest_streak = current_rest_streak
                        max_rest_end = day
                else:
                    current_rest_streak = 0

            if max_rest_end and max_rest_streak > expected_rest_span:
                rest_streak_start = max_rest_end - timedelta(days=max_rest_streak - 1)
                extra_days = max_rest_streak - expected_rest_span
                severity = "warning" if extra_days >= 2 else "info"
                issues.append(
                    {
                        "severity": severity,
                        "title": f"{name} acumula {max_rest_streak} días de descanso seguidos",
                        "detail": (
                            f"Periodo {rest_streak_start:%d/%m} → {max_rest_end:%d/%m}. "
                            f"Configuración esperada: {expected_rest_span} día(s) para "
                            f"{category_name or 'la categoría asignada'}."
                        ),
                        "score": extra_days,
                    }
                )

        if rest_days and rest_monthly_limit:
            rest_by_month: Counter = Counter((day.year, day.month) for day in rest_days)
            for (year, month), count in rest_by_month.items():
                if count > rest_monthly_limit:
                    issues.append(
                        {
                            "severity": "warning",
                            "title": f"{name} supera descansos previstos en {year}-{month:02d}",
                            "detail": (
                                f"{count} descansos frente a un límite de {rest_monthly_limit} "
                                f"para {category_name or 'la categoría asignada'}."
                            ),
                            "score": count - rest_monthly_limit,
                        }
                    )
                    break

        if unassigned_days:
            max_idle_streak = 0
            max_idle_end: Optional[date] = None
            current_idle_streak = 0
            for day in calendar_days:
                if day in work_day_set or day in rest_days:
                    current_idle_streak = 0
                    continue
                if day in unassigned_days:
                    current_idle_streak += 1
                    if current_idle_streak > max_idle_streak:
                        max_idle_streak = current_idle_streak
                        max_idle_end = day
                else:
                    current_idle_streak = 0

            if max_idle_end and max_idle_streak >= 2:
                idle_start = max_idle_end - timedelta(days=max_idle_streak - 1)
                severity = "warning" if max_idle_streak >= 4 else "info"
                issues.append(
                    {
                        "severity": severity,
                        "title": f"{name} lleva {max_idle_streak} días sin asignación",
                        "detail": (
                            f"Periodo {idle_start:%d/%m} → {max_idle_end:%d/%m}. "
                            "Verifica disponibilidad o reasigna turnos."
                        ),
                        "score": max_idle_streak,
                    }
                )

        overtime_days: set[date] = data.get("overtime_days", set())
        if overtime_days:
            ordered_overtime = sorted(overtime_days)
            first_overtime = ordered_overtime[0]
            issues.append(
                {
                    "severity": "warning",
                    "title": f"{name} tiene {len(overtime_days)} días marcados como sobrecarga",
                    "detail": (
                        f"Revisa compensaciones. Primer día detectado: {first_overtime:%d/%m/%Y}."
                    ),
                    "score": len(overtime_days),
                }
            )

    def _issue_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
        severity = item.get("severity", "info")
        score = item.get("score", 0)
        label = item.get("title", "")
        return (-severity_weight.get(severity, 1), -score, label.lower())

    issues.sort(key=_issue_sort_key)

    result: list[dict[str, Any]] = []
    for issue in issues[:6]:
        styles = ISSUE_STYLE_MAP.get(issue.get("severity", "info"), ISSUE_STYLE_MAP["info"]).copy()
        sanitized = {key: value for key, value in issue.items() if key not in {"score"}}
        sanitized.update(
            {
                "indicator_class": styles.get("indicator_class", "bg-slate-400"),
                "badge_class": styles.get("badge_class", "border border-slate-200 bg-slate-100 text-slate-600"),
                "severity_label": styles.get("label", "Seguimiento"),
            }
        )
        result.append(sanitized)

    return result


CLASSIFIER_CATEGORY_CODES: set[str] = {
    PositionCategoryCode.CLASIFICADOR_DIA,
    PositionCategoryCode.CLASIFICADOR_NOCHE,
}

FARM_CATEGORY_DISPLAY_ORDER: tuple[str, ...] = (
    PositionCategoryCode.LIDER_GRANJA,
    PositionCategoryCode.GALPONERO_LEVANTE_DIA,
    PositionCategoryCode.GALPONERO_LEVANTE_NOCHE,
    PositionCategoryCode.GALPONERO_PRODUCCION_DIA,
    PositionCategoryCode.GALPONERO_PRODUCCION_NOCHE,
    PositionCategoryCode.OFICIOS_VARIOS,
)

CLASSIFIER_CATEGORY_DISPLAY_ORDER: tuple[str, ...] = (
    PositionCategoryCode.CLASIFICADOR_DIA,
    PositionCategoryCode.CLASIFICADOR_NOCHE,
)

FARM_GROUP_COLOR_SEQUENCE: tuple[str, ...] = (
    "bg-teal-50",
    "bg-sky-50",
    "bg-lime-50",
    "bg-emerald-50",
    "bg-amber-50",
    "bg-rose-50",
)

CLASSIFIER_GROUP_COLOR: str = "bg-indigo-50"
REST_GROUP_COLOR: str = "bg-slate-50"
MISC_GROUP_COLOR: str = "bg-slate-100"


def _build_operational_position_groups(
    rows: Iterable[dict[str, Any]],
    rest_rows: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Prepare row ordering and metadata used by the operational view and highlights."""

    row_list = list(rows)
    rest_row_list = list(rest_rows)

    farm_map: dict[int, dict[str, Any]] = {}
    classifier_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
    misc_rows: list[dict[str, Any]] = []

    def _row_sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
        position: Optional[PositionDefinition] = row.get("position")
        if not position:
            return ("", "", 0)
        name = (position.name or "").lower()
        code = (position.code or "").lower()
        return (name, code, position.pk or 0)

    for row in row_list:
        position: Optional[PositionDefinition] = row.get("position")
        if not position:
            misc_rows.append(row)
            continue

        category = getattr(position, "category", None)
        category_code = getattr(category, "code", "")
        if category_code in CLASSIFIER_CATEGORY_CODES:
            classifier_map[category_code].append(row)
            continue

        farm_id = getattr(position, "farm_id", None)
        if farm_id:
            entry = farm_map.setdefault(
                farm_id,
                {
                    "farm": position.farm,
                    "farm_id": farm_id,
                    "rows_by_category": defaultdict(list),
                },
            )
            entry["rows_by_category"][category_code].append(row)
        else:
            misc_rows.append(row)

    ordered_rows: list[dict[str, Any]] = []
    farms_payload: list[dict[str, Any]] = []
    groups_payload: list[dict[str, Any]] = []
    group_map: dict[str, dict[str, Any]] = {}
    position_to_group: dict[int, str] = {}

    color_cycle = cycle(FARM_GROUP_COLOR_SEQUENCE or ("bg-emerald-50",))
    farm_entries = sorted(
        farm_map.values(),
        key=lambda entry: ((entry["farm"].name or "").lower() if entry["farm"] else ""),
    )

    for entry in farm_entries:
        rows_by_category: defaultdict[str, list[dict[str, Any]]] = entry["rows_by_category"]
        group_rows: list[dict[str, Any]] = []

        for category_code in FARM_CATEGORY_DISPLAY_ORDER:
            category_rows = rows_by_category.pop(category_code, [])
            category_rows.sort(key=_row_sort_key)
            group_rows.extend(category_rows)

        if rows_by_category:
            for leftover_code in sorted(rows_by_category.keys()):  # deterministic fallback
                category_rows = rows_by_category[leftover_code]
                category_rows.sort(key=_row_sort_key)
                group_rows.extend(category_rows)

        if not group_rows:
            continue

        color_class = next(color_cycle)
        group_key = f"farm-{entry['farm_id']}"
        group_label = entry["farm"].name if entry["farm"] else "Granja sin nombre"

        position_ids: list[int] = []
        for row in group_rows:
            row["display_group"] = {
                "key": group_key,
                "type": "farm",
                "label": group_label,
                "color_class": color_class,
            }
            position = row.get("position")
            if position and position.id is not None:
                position_ids.append(position.id)
                position_to_group[position.id] = group_key

        ordered_rows.extend(group_rows)
        farm_instance = entry["farm"]
        farms_payload.append(
            {
                "key": group_key,
                "farm": (
                    {
                        "id": farm_instance.id,
                        "name": farm_instance.name,
                    }
                    if farm_instance
                    else {
                        "id": None,
                        "name": "Granja sin nombre",
                    }
                ),
                "positions": [
                    _position_payload(row["position"])
                    for row in group_rows
                    if row.get("position")
                ],
                "position_ids": position_ids,
                "color_class": color_class,
            }
        )
        group_entry = {
            "key": group_key,
            "type": "farm",
            "label": group_label,
            "color_class": color_class,
            "position_ids": position_ids,
        }
        groups_payload.append(group_entry)
        group_map[group_key] = group_entry

    misc_group_payload: Optional[dict[str, Any]] = None
    if misc_rows:
        misc_rows.sort(key=_row_sort_key)
        misc_key = "misc-positions"
        misc_label = "Otras posiciones"
        misc_ids: list[int] = []
        for row in misc_rows:
            row["display_group"] = {
                "key": misc_key,
                "type": "misc",
                "label": misc_label,
                "color_class": MISC_GROUP_COLOR,
            }
            position = row.get("position")
            if position and position.id is not None:
                misc_ids.append(position.id)
                position_to_group[position.id] = misc_key

        ordered_rows.extend(misc_rows)
        misc_group_payload = {
            "key": misc_key,
            "type": "misc",
            "label": misc_label,
            "color_class": MISC_GROUP_COLOR,
            "position_ids": misc_ids,
        }
        groups_payload.append(misc_group_payload)
        group_map[misc_key] = misc_group_payload

    classifier_rows: list[dict[str, Any]] = []
    for category_code in CLASSIFIER_CATEGORY_DISPLAY_ORDER:
        category_rows = classifier_map.pop(category_code, [])
        category_rows.sort(key=_row_sort_key)
        classifier_rows.extend(category_rows)

    if classifier_map:
        for leftover_code in sorted(classifier_map.keys()):
            category_rows = classifier_map[leftover_code]
            category_rows.sort(key=_row_sort_key)
            classifier_rows.extend(category_rows)

    classifier_positions: list[PositionDefinition] = [
        row["position"] for row in classifier_rows if row.get("position")
    ]
    classifier_position_ids: list[int] = [
        position.id for position in classifier_positions if position and position.id is not None
    ]

    if classifier_rows:
        classifier_key = "classifiers"
        classifier_label = "Clasificadores"
        for row in classifier_rows:
            row["display_group"] = {
                "key": classifier_key,
                "type": "classifier",
                "label": classifier_label,
                "color_class": CLASSIFIER_GROUP_COLOR,
            }
            position = row.get("position")
            if position and position.id is not None:
                position_to_group[position.id] = classifier_key

        ordered_rows.extend(classifier_rows)
        classifier_group_entry = {
            "key": classifier_key,
            "type": "classifier",
            "label": classifier_label,
            "color_class": CLASSIFIER_GROUP_COLOR,
            "position_ids": classifier_position_ids,
        }
        groups_payload.append(classifier_group_entry)
        group_map[classifier_key] = classifier_group_entry
        classifier_payload: dict[str, Any] = {
            "key": classifier_key,
            "label": classifier_label,
            "color_class": CLASSIFIER_GROUP_COLOR,
            "positions": [_position_payload(position) for position in classifier_positions],
            "position_ids": classifier_position_ids,
        }
    else:
        classifier_payload = {
            "key": "classifiers",
            "label": "Clasificadores",
            "color_class": CLASSIFIER_GROUP_COLOR,
            "positions": [],
            "position_ids": [],
        }

    appended_ids = {id(row) for row in ordered_rows}
    for row in row_list:
        if id(row) in appended_ids:
            continue
        row.setdefault(
            "display_group",
            {
                "key": "misc-positions",
                "type": "misc",
                "label": "Otras posiciones",
                "color_class": MISC_GROUP_COLOR,
            },
        )
        group_entry = group_map.setdefault(
            "misc-positions",
            {
                "key": "misc-positions",
                "type": "misc",
                "label": "Otras posiciones",
                "color_class": MISC_GROUP_COLOR,
                "position_ids": [],
            },
        )
        if misc_group_payload is None:
            misc_group_payload = group_entry
        position = row.get("position")
        if position and position.id is not None:
            if position.id not in group_entry["position_ids"]:
                group_entry["position_ids"].append(position.id)
            position_to_group.setdefault(position.id, row["display_group"]["key"])
        ordered_rows.append(row)
        appended_ids.add(id(row))

    rest_group_payload = {
        "key": "rests",
        "type": "rest",
        "label": "Descansos y disponibilidad",
        "color_class": REST_GROUP_COLOR,
    }
    group_map[rest_group_payload["key"]] = rest_group_payload
    rest_items: list[dict[str, Any]] = []
    for rest_row in rest_row_list:
        rest_row["display_group"] = rest_group_payload
        rest_items.append(
            {
                "label": rest_row.get("label"),
                "slot": rest_row.get("slot"),
            }
        )

    groups_payload.append(rest_group_payload)

    if misc_group_payload and misc_group_payload not in groups_payload:
        groups_payload.append(misc_group_payload)
        group_map[misc_group_payload["key"]] = misc_group_payload

    position_groups_payload: dict[str, Any] = {
        "farms": farms_payload,
        "classifiers": classifier_payload,
        "rests": rest_items,
        "rest_group": rest_group_payload,
        "misc_group": misc_group_payload,
        "groups": groups_payload,
        "group_map": group_map,
        "position_to_group": position_to_group,
    }

    return ordered_rows, rest_row_list, position_groups_payload


def _refresh_workload_snapshots(calendar: ShiftCalendar) -> None:
    scheduler = CalendarScheduler(calendar, options=SchedulerOptions())
    scheduler._rebuild_workload_snapshots()
    sync_calendar_rest_periods(calendar)


def _release_assignments_for_rest_period(rest_period: OperatorRestPeriod) -> tuple[int, set[int]]:
    if not rest_period.operator_id:
        return 0, set()

    assignment_qs = ShiftAssignment.objects.filter(
        operator_id=rest_period.operator_id,
        date__range=(rest_period.start_date, rest_period.end_date),
    )
    if rest_period.calendar_id:
        assignment_qs = assignment_qs.filter(calendar_id=rest_period.calendar_id)

    assignments = list(
        assignment_qs.select_related("calendar").only("id", "calendar_id")
    )
    if not assignments:
        return 0, set()

    assignment_ids = [assignment.id for assignment in assignments]
    calendar_ids = {assignment.calendar_id for assignment in assignments if assignment.calendar_id}

    ShiftAssignment.objects.filter(pk__in=assignment_ids).delete()

    return len(assignment_ids), calendar_ids


def _apply_assignment_conflict_resets(
    *,
    conflicting_assignment: ShiftAssignment | None,
    conflicting_rest_periods: Iterable[OperatorRestPeriod],
) -> tuple[str, int]:
    removed_assignment_label = ""
    rest_periods = list(conflicting_rest_periods)

    if conflicting_assignment:
        removed_assignment_label = f"{conflicting_assignment.position.name} ({conflicting_assignment.date})"
        conflicting_assignment.delete()

    for rest_period in rest_periods:
        rest_period.delete()

    return removed_assignment_label, len(rest_periods)


def _json_error(message: str, *, status: int = 400, errors: Optional[dict[str, Any]] = None) -> JsonResponse:
    payload: dict[str, Any] = {"error": message}
    if errors:
        payload["errors"] = errors
    return JsonResponse(payload, status=status)


def _form_errors(form: forms.Form) -> dict[str, list[str]]:  # type: ignore[name-defined]
    error_dict: dict[str, list[str]] = {}
    for field, messages_list in form.errors.items():
        error_dict[field] = [str(message) for message in messages_list]
    non_field_errors = form.non_field_errors()
    if non_field_errors:
        error_dict.setdefault("__all__", []).extend(str(message) for message in non_field_errors)
    return error_dict


def _load_json_body(request: HttpRequest) -> tuple[Optional[dict[str, Any]], Optional[JsonResponse]]:
    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return None, _json_error("JSON inválido")
    if not isinstance(payload, dict):
        return None, _json_error("El cuerpo debe ser un objeto JSON")
    return payload, None


def _choice_payload(choices: Iterable[tuple[str, str]]) -> List[dict[str, str]]:
    return [
        {
            "value": value,
            "label": label,
        }
        for value, label in choices
    ]


def _position_payload(position: PositionDefinition, *, reference_date: date | None = None) -> dict[str, Any]:
    active_reference = reference_date or UserProfile.colombia_today()
    handoff = position.handoff_position
    return {
        "id": position.id,
        "name": position.name,
        "code": position.code,
        "display_order": position.display_order,
        "category": {
            "id": position.category_id,
            "code": position.category.code,
            "name": position.category.display_name,
            "shift_type": position.category.shift_type,
        },
        "category_id": str(position.category_id) if position.category_id else "",
        "farm": {
            "id": position.farm_id,
            "name": position.farm.name if position.farm_id else None,
        },
        "chicken_house": {
            "id": position.chicken_house_id,
            "name": position.chicken_house.name if position.chicken_house_id else None,
        },
        "rooms": [
            {
                "id": room.id,
                "name": room.name,
            }
            for room in position.rooms.all()
        ],
        "shift_type": position.shift_type,
        "valid_from": position.valid_from.isoformat(),
        "valid_until": position.valid_until.isoformat() if position.valid_until else None,
        "is_active": position.is_active_on(active_reference),
        "handoff_position": (
            {
                "id": handoff.id,
                "name": handoff.name,
                "code": handoff.code,
            }
            if handoff
            else None
        ),
        "handoff_position_id": str(handoff.id) if handoff else "",
    }


def _operator_payload(
    operator: UserProfile,
    *,
    roles: Optional[Iterable[Role]] = None,
    reference_date: date | None = None,
) -> dict[str, Any]:
    role_items = roles if roles is not None else list(operator.roles.all())
    suggested_positions = list(operator.suggested_positions.all())
    active_reference = reference_date or UserProfile.colombia_today()
    return {
        "id": operator.id,
        "name": operator.get_full_name() or operator.nombres,
        "document": operator.cedula,
        "nombres": operator.nombres,
        "apellidos": operator.apellidos,
        "telefono": operator.telefono,
        "employment_start": operator.employment_start_date.isoformat()
        if operator.employment_start_date
        else None,
        "employment_end": operator.employment_end_date.isoformat()
        if operator.employment_end_date
        else None,
        "automatic_rest_days": list(operator.automatic_rest_days or []),
        "automatic_rest_day_labels": operator.automatic_rest_day_labels(),
        "has_access_key": operator.has_usable_password(),
        "suggested_positions": [
            {
                "id": position.id,
                "code": position.code,
                "name": position.name,
            }
            for position in suggested_positions
        ],
        "roles": [
            {
                "id": role.id,
                "code": role.name,
                "label": role.get_name_display(),
            }
            for role in role_items
        ],
        "is_active": operator.is_active_on(active_reference),
    }


def _format_operator_label(operator: UserProfile) -> str:
    roles = list(operator.roles.all())
    role_label = roles[0].get_name_display() if roles else ""
    label = operator.get_full_name() or operator.nombres
    if role_label:
        label = f"{label} · {role_label}"
    return label

def _rest_period_payload(period: OperatorRestPeriod) -> dict[str, Any]:
    calendar = period.calendar
    created_by = period.created_by
    return {
        "id": period.id,
        "operator_id": period.operator_id,
        "start": period.start_date.isoformat(),
        "end": period.end_date.isoformat(),
        "status": period.status,
        "status_label": RestPeriodStatus(period.status).label,
        "source": period.source,
        "source_label": RestPeriodSource(period.source).label,
        "notes": period.notes,
        "calendar_id": calendar.id if calendar else None,
        "calendar": (
            {
                "id": calendar.id,
                "name": calendar.name,
                "start_date": calendar.start_date.isoformat(),
                "end_date": calendar.end_date.isoformat(),
                "status": calendar.status,
            }
            if calendar
            else None
        ),
        "created_by": (
            {
                "id": created_by.id,
                "name": created_by.get_full_name() or created_by.get_username(),
            }
            if created_by
            else None
        ),
        "created_at": period.created_at.isoformat(),
        "updated_at": period.updated_at.isoformat(),
        "is_system_generated": period.source == RestPeriodSource.CALENDAR,
        "duration_days": (period.end_date - period.start_date).days + 1,
    }


def _rest_period_requires_calendar_refresh(
    operator_id: int,
    start_date: date,
    end_date: date,
    source: str,
) -> bool:
    if not operator_id:
        return False
    if source == RestPeriodSource.CALENDAR:
        return True

    return ShiftAssignment.objects.filter(
        operator_id=operator_id,
        date__range=(start_date, end_date),
    ).exists()


def _assignment_payload(assignment: Optional[ShiftAssignment]) -> Optional[dict[str, Any]]:
    if assignment is None:
        return None

    operator_payload: Optional[dict[str, Any]] = None
    if assignment.operator_id:
        roles = list(assignment.operator.roles.all()) if assignment.operator_id else []
        operator_payload = (
            _operator_payload(assignment.operator, roles=roles, reference_date=assignment.date)
            if assignment.operator_id
            else None
        )

    return {
        "id": assignment.id,
        "calendar_id": assignment.calendar_id,
        "position": _position_payload(assignment.position),
        "date": assignment.date.isoformat(),
        "operator": operator_payload,
        "alert_level": assignment.alert_level,
        "is_overtime": assignment.is_overtime,
        "overtime_points": assignment.overtime_points,
        "is_auto_assigned": assignment.is_auto_assigned,
        "notes": assignment.notes,
    }


# ---------------------------------------------------------------------------
# Vistas HTML para usuarios no administradores
# ---------------------------------------------------------------------------


class CalendarConfiguratorView(StaffRequiredMixin, View):
    template_name = "calendario/configurator.html"

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> Any:
        return render(
            request,
            self.template_name,
            {
                "alert_choices": _choice_payload(AssignmentAlertLevel.choices),
                "shift_type_choices": _choice_payload(ShiftType.choices),
                "status_choices": _choice_payload(CalendarStatus.choices),
                "calendar_generation_form": CalendarGenerationForm(),
                "calendar_home_url": _resolve_calendar_home_url(),
                "calendar_generation_recent_calendars": _recent_calendars_payload(),
            },
        )


class CalendarDashboardView(StaffRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> Any:
        return redirect(_resolve_calendar_home_url())


class CalendarCreateView(StaffRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> JsonResponse:
        form = CalendarGenerationForm(request.POST)
        if not form.is_valid():
            errors = {field: [str(error) for error in error_list] for field, error_list in form.errors.items()}
            return JsonResponse({"success": False, "errors": errors}, status=400)

        calendar = ShiftCalendar.objects.create(
            name=form.cleaned_data.get("name", ""),
            start_date=form.cleaned_data["start_date"],
            end_date=form.cleaned_data["end_date"],
            status=CalendarStatus.DRAFT,
            created_by=request.user,
            notes=form.cleaned_data.get("notes", ""),
        )

        scheduler = CalendarScheduler(calendar, options=SchedulerOptions())
        decisions = scheduler.generate(commit=True)

        gaps = sum(1 for decision in decisions if decision.operator is None)
        if gaps:
            messages.warning(
                request,
                f"Calendario generado con {gaps} posiciones sin cobertura. Revisa el detalle para gestionarlas.",
            )
        else:
            messages.success(request, "Calendario generado exitosamente.")

        redirect_url = reverse("personal:calendar-detail", args=[calendar.id])
        return JsonResponse(
            {
                "success": True,
                "redirect_url": redirect_url,
                "calendar": {
                    "id": calendar.id,
                    "name": calendar.name,
                    "start_date": calendar.start_date.isoformat(),
                    "end_date": calendar.end_date.isoformat(),
                },
                "gaps": gaps,
            },
            status=201,
        )

class CalendarDetailView(StaffRequiredMixin, View):
    template_name = "calendario/calendar_detail.html"

    def get(self, request: HttpRequest, pk: int, *args: Any, **kwargs: Any) -> Any:
        calendar = get_object_or_404(
            ShiftCalendar.objects.select_related("created_by", "approved_by", "base_calendar"),
            pk=pk,
        )
        date_columns, rows, rest_rows, rest_summary, position_groups = _build_assignment_matrix(calendar)
        stats = _calculate_stats(rows)
        assignment_issues = _identify_calendar_issues(date_columns, rows, rest_rows)
        can_override = calendar.status in {CalendarStatus.DRAFT, CalendarStatus.MODIFIED}
        has_manual_choices = any(
            cell.get("choices")
            for row in rows
            for cell in row.get("cells", [])
        )
        latest_modification = (
            AssignmentChangeLog.objects.filter(
                Q(assignment__calendar=calendar) | Q(details__calendar_id=calendar.id)
            )
            .order_by("-created_at")
            .values_list("created_at", flat=True)
            .first()
        )

        return render(
            request,
            self.template_name,
            {
                "calendar": calendar,
                "date_columns": date_columns,
                "rows": rows,
                "stats": stats,
                "assignment_issues": assignment_issues,
                "rest_rows": rest_rows,
                "rest_summary": rest_summary,
                "can_override": can_override,
                "has_manual_choices": has_manual_choices,
                "calendar_latest_modification": latest_modification,
                "position_groups": position_groups,
                "calendar_generation_form": CalendarGenerationForm(),
                "calendar_home_url": _resolve_calendar_home_url(exclude_ids=[calendar.id]),
                "calendar_generation_recent_calendars": _recent_calendars_payload(exclude_ids=[calendar.id]),
            },
        )

    def post(self, request: HttpRequest, pk: int, *args: Any, **kwargs: Any) -> Any:
        calendar = get_object_or_404(ShiftCalendar, pk=pk)
        action = request.POST.get("action")

        if action == "approve":
            if calendar.status in {CalendarStatus.DRAFT, CalendarStatus.MODIFIED}:
                calendar.mark_approved(request.user)
                messages.success(request, "Calendario aprobado y publicado para operación.")
            else:
                messages.info(request, "El calendario ya se encuentra aprobado.")
        elif action == "update-assignment":
            operator_id_raw = (request.POST.get("operator_id") or "").strip()
            assignment_id_raw = (request.POST.get("assignment_id") or "").strip()
            redirect_url = reverse("personal:calendar-detail", args=[calendar.id])

            if not operator_id_raw:
                try:
                    assignment_id = int(assignment_id_raw)
                except (TypeError, ValueError):
                    messages.error(request, "No se identificó la asignación que deseas liberar.")
                    return redirect(redirect_url)

                assignment = calendar.assignments.filter(pk=assignment_id).first()
                if not assignment:
                    messages.info(request, "La asignación ya no se encuentra registrada.")
                    return redirect(redirect_url)

                assignment.delete()
                _refresh_workload_snapshots(calendar)
                messages.success(request, "Turno liberado. La celda quedó sin colaborador.")
                return redirect(redirect_url)

            form = AssignmentUpdateForm(request.POST, calendar=calendar)
            if form.is_valid():
                assignment: ShiftAssignment = form.cleaned_data["assignment"]
                operator: UserProfile = form.cleaned_data["operator"]
                alert_level: AssignmentAlertLevel = form.cleaned_data["alert_level"]
                is_overtime: bool = form.cleaned_data["is_overtime"]
                overtime_points: int = form.cleaned_data["overtime_points"]
                conflicting_assignment: ShiftAssignment | None = form.cleaned_data.get("conflicting_assignment")
                conflicting_rest_periods: list[OperatorRestPeriod] = list(
                    form.cleaned_data.get(
                        "conflicting_rest_periods",
                        [],
                    )
                )

                removed_assignment_label = ""
                removed_rest_count = 0
                with transaction.atomic():
                    removed_assignment_label, removed_rest_count = _apply_assignment_conflict_resets(
                        conflicting_assignment=conflicting_assignment,
                        conflicting_rest_periods=conflicting_rest_periods,
                    )

                    assignment.operator = operator
                    assignment.alert_level = alert_level
                    assignment.is_overtime = is_overtime
                    assignment.overtime_points = overtime_points if is_overtime else 0
                    assignment.is_auto_assigned = False
                    assignment.save(
                        update_fields=[
                            "operator",
                            "alert_level",
                            "is_overtime",
                            "overtime_points",
                            "is_auto_assigned",
                            "updated_at",
                        ]
                    )

                _refresh_workload_snapshots(calendar)
                message_parts = ["Asignación actualizada correctamente."]
                if removed_assignment_label:
                    message_parts.append(
                        f"Se liberó el turno previo de {removed_assignment_label}."
                    )
                if removed_rest_count == 1:
                    message_parts.append(
                        "Se eliminó 1 descanso que se superponía con la nueva asignación."
                    )
                elif removed_rest_count > 1:
                    message_parts.append(
                        f"Se eliminaron {removed_rest_count} descansos que se superponían con la nueva asignación."
                    )
                messages.success(request, " ".join(message_parts))
            else:
                messages.error(request, form.errors.as_text())
        elif action == "create-assignment":
            form = AssignmentCreateForm(request.POST, calendar=calendar)
            if form.is_valid():
                position: PositionDefinition = form.cleaned_data["position"]
                operator: UserProfile = form.cleaned_data["operator"]
                alert_level: AssignmentAlertLevel = form.cleaned_data["alert_level"]
                target_date = form.cleaned_data["target_date"]
                is_overtime: bool = form.cleaned_data["is_overtime"]
                overtime_points: int = form.cleaned_data["overtime_points"]
                conflicting_assignment: ShiftAssignment | None = form.cleaned_data.get("conflicting_assignment")
                conflicting_rest_periods: list[OperatorRestPeriod] = list(
                    form.cleaned_data.get("conflicting_rest_periods", [])
                )

                removed_assignment_label = ""
                removed_rest_count = 0

                with transaction.atomic():
                    removed_assignment_label, removed_rest_count = _apply_assignment_conflict_resets(
                        conflicting_assignment=conflicting_assignment,
                        conflicting_rest_periods=conflicting_rest_periods,
                    )

                    ShiftAssignment.objects.create(
                        calendar=calendar,
                        position=position,
                        date=target_date,
                        operator=operator,
                        alert_level=alert_level,
                        is_overtime=is_overtime,
                        overtime_points=overtime_points if is_overtime else 0,
                        is_auto_assigned=False,
                    )

                _refresh_workload_snapshots(calendar)
                message_parts = ["Turno asignado manualmente."]
                if removed_assignment_label:
                    message_parts.append(
                        f"Se liberó el turno previo de {removed_assignment_label}."
                    )
                if removed_rest_count == 1:
                    message_parts.append(
                        "Se eliminó 1 descanso que se superponía con la nueva asignación."
                    )
                elif removed_rest_count > 1:
                    message_parts.append(
                        f"Se eliminaron {removed_rest_count} descansos que se superponían con la nueva asignación."
                    )
                messages.success(request, " ".join(message_parts))
            else:
                messages.error(request, form.errors.as_text())
        elif action == "regenerate":
            with transaction.atomic():
                calendar.assignments.all().delete()
                calendar.rest_periods.all().delete()
                calendar.workload_snapshots.all().delete()

            scheduler = CalendarScheduler(calendar, options=SchedulerOptions())
            decisions = scheduler.generate(commit=True)

            gaps = sum(1 for decision in decisions if decision.operator is None)
            if gaps:
                messages.warning(
                    request,
                    f"Calendario regenerado. {gaps} posiciones pendientes requieren asignación.",
                )
            else:
                messages.success(request, "Calendario regenerado exitosamente.")
        elif action == "mark-modified":
            if calendar.status != CalendarStatus.APPROVED:
                messages.info(request, "Solo los calendarios aprobados pueden marcarse como modificados.")
            else:
                calendar.status = CalendarStatus.MODIFIED
                calendar.save(update_fields=["status", "updated_at"])
                messages.info(request, "El calendario ahora se encuentra en estado modificado y está listo para ajustes.")
        else:
            messages.error(request, "Acción no reconocida.")

        return redirect(reverse("personal:calendar-detail", args=[calendar.id]))


class CalendarDeleteView(StaffRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, pk: int, *args: Any, **kwargs: Any) -> Any:
        calendar = get_object_or_404(ShiftCalendar, pk=pk)
        fallback_url = _resolve_calendar_home_url(exclude_ids=[calendar.pk])
        redirect_url = request.POST.get("next") or fallback_url

        calendar_label = calendar.name or f"Calendario {calendar.start_date} -> {calendar.end_date}"

        try:
            calendar.delete()
        except ProtectedError:
            messages.error(
                request,
                "No es posible eliminar este calendario porque tiene modificaciones asociadas.",
            )
            return redirect(reverse("personal:calendar-detail", args=[calendar.pk]))

        messages.success(request, f'Se eliminó el calendario "{calendar_label}".')
        return redirect(redirect_url)


# ---------------------------------------------------------------------------
# API JSON
# ---------------------------------------------------------------------------


class CalendarGenerateView(StaffRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> JsonResponse:
        try:
            payload = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return HttpResponseBadRequest("JSON inválido")

        try:
            start_date = _parse_date(payload.get("start_date"), "start_date")
            end_date = _parse_date(payload.get("end_date"), "end_date")
        except (TypeError, ValueError) as exc:
            return HttpResponseBadRequest(str(exc))

        name_value = payload.get("name")
        notes_value = payload.get("notes")

        defaults = {
            "name": name_value or "",
            "created_by": request.user,
            "notes": notes_value or "",
        }

        try:
            calendar, created = ShiftCalendar.objects.get_or_create(
                start_date=start_date,
                end_date=end_date,
                status=CalendarStatus.DRAFT,
                defaults=defaults,
            )
        except IntegrityError:
            calendar = ShiftCalendar.objects.get(
                start_date=start_date,
                end_date=end_date,
                status=CalendarStatus.DRAFT,
            )
            created = False

        if not created:
            updated_fields: List[str] = []

            if "name" in payload and calendar.name != (name_value or ""):
                calendar.name = name_value or ""
                updated_fields.append("name")

            if "notes" in payload and calendar.notes != (notes_value or ""):
                calendar.notes = notes_value or ""
                updated_fields.append("notes")

            if calendar.created_by_id != request.user.id:
                calendar.created_by = request.user
                updated_fields.append("created_by")

            if updated_fields:
                updated_fields.append("updated_at")
                calendar.save(update_fields=updated_fields)

        options = SchedulerOptions()
        scheduler = CalendarScheduler(calendar, options=options)
        decisions = scheduler.generate(commit=True)

        response_payload: Dict[str, Any] = {
            "calendar_id": calendar.id,
            "status": calendar.status,
            "assignments_created": sum(1 for decision in decisions if decision.operator),
            "gaps_detected": [
                {
                    "date": decision.date.isoformat(),
                    "position": decision.position.code,
                    "alert_level": decision.alert_level,
                    "notes": decision.notes,
                }
                for decision in decisions
                if decision.operator is None
            ],
        }

        return JsonResponse(response_payload, status=201)


class OperatorCollectionView(StaffRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> JsonResponse:
        payload, error = _load_json_body(request)
        if error:
            return error

        form = OperatorProfileForm(payload)
        if not form.is_valid():
            return _json_error("Datos inválidos para el colaborador.", errors=_form_errors(form))

        with transaction.atomic():
            operator = form.save()

        operator = UserProfile.objects.prefetch_related("roles", "suggested_positions").get(pk=operator.pk)
        reference_date = UserProfile.colombia_today()
        return JsonResponse({"operator": _operator_payload(operator, reference_date=reference_date)}, status=201)


class OperatorDetailView(StaffRequiredMixin, View):
    http_method_names = ["patch"]

    def patch(self, request: HttpRequest, operator_id: int, *args: Any, **kwargs: Any) -> JsonResponse:
        operator = get_object_or_404(UserProfile, pk=operator_id)
        payload, error = _load_json_body(request)
        if error:
            return error

        # Merge current operator values with incoming payload so the form receives a complete dataset.
        form_fields = list(OperatorProfileForm._meta.fields)  # type: ignore[attr-defined]
        form_data: dict[str, Any] = {"access_key": ""}
        for field_name in form_fields:
            if field_name == "roles":
                form_data[field_name] = list(operator.roles.values_list("pk", flat=True))
            elif field_name == "suggested_positions":
                form_data[field_name] = list(operator.suggested_positions.values_list("pk", flat=True))
            else:
                form_data[field_name] = getattr(operator, field_name)

        for key, value in payload.items():
            if key in form_data:
                form_data[key] = value

        form = OperatorProfileForm(form_data, instance=operator)
        if not form.is_valid():
            return _json_error("Datos inválidos para el colaborador.", errors=_form_errors(form))

        with transaction.atomic():
            operator = form.save()

        operator = UserProfile.objects.prefetch_related("roles", "suggested_positions").get(pk=operator.pk)
        reference_date = UserProfile.colombia_today()
        return JsonResponse({"operator": _operator_payload(operator, reference_date=reference_date)})


class PositionCollectionView(StaffRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> JsonResponse:
        payload, error = _load_json_body(request)
        if error:
            return error

        form = PositionDefinitionForm(payload)
        if not form.is_valid():
            return _json_error("Datos inválidos para la posición.", errors=_form_errors(form))

        position = form.save()
        return JsonResponse({"position": _position_payload(position)}, status=201)


class PositionReorderView(StaffRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> JsonResponse:
        payload, error = _load_json_body(request)
        if error:
            return error

        order_values = payload.get("order")
        if not isinstance(order_values, list) or not order_values:
            return _json_error("El payload debe incluir la lista de posiciones en 'order'.")

        try:
            desired_order = [int(value) for value in order_values]
        except (TypeError, ValueError):
            return _json_error("Los identificadores de posiciones deben ser numéricos.")

        if len(desired_order) != len(set(desired_order)):
            return _json_error("La lista de posiciones contiene duplicados.")

        positions = list(
            PositionDefinition.objects.select_related("farm", "chicken_house", "category", "handoff_position")
            .prefetch_related("rooms")
            .order_by("display_order", "id")
        )
        positions_by_id = {position.id: position for position in positions}
        missing = [position_id for position_id in desired_order if position_id not in positions_by_id]
        if missing:
            return _json_error(
                "Algunas posiciones indicadas no existen.",
                errors={"missing": missing},
            )

        ordered_positions: List[PositionDefinition] = []
        seen: set[int] = set()
        for position_id in desired_order:
            ordered_positions.append(positions_by_id[position_id])
            seen.add(position_id)
        for position in positions:
            if position.id not in seen:
                ordered_positions.append(position)

        for index, position in enumerate(ordered_positions, start=1):
            position.display_order = index

        with transaction.atomic():
            PositionDefinition.objects.bulk_update(ordered_positions, ["display_order"])

        refreshed_positions = [
            _position_payload(position)
            for position in PositionDefinition.objects.select_related("farm", "chicken_house", "category", "handoff_position")
            .prefetch_related("rooms")
            .order_by("display_order", "id")
        ]
        return JsonResponse({"positions": refreshed_positions})


class PositionDetailView(StaffRequiredMixin, View):
    http_method_names = ["patch", "delete"]

    def patch(self, request: HttpRequest, position_id: int, *args: Any, **kwargs: Any) -> JsonResponse:
        position = get_object_or_404(PositionDefinition, pk=position_id)
        payload, error = _load_json_body(request)
        if error:
            return error

        form = PositionDefinitionForm(payload, instance=position)
        if not form.is_valid():
            return _json_error("Datos inválidos para la posición.", errors=_form_errors(form))

        position = form.save()
        return JsonResponse({"position": _position_payload(position)})

    def delete(self, request: HttpRequest, position_id: int, *args: Any, **kwargs: Any) -> JsonResponse:
        position = get_object_or_404(PositionDefinition, pk=position_id)
        try:
            position.delete()
        except ProtectedError:
            return _json_error(
                "No es posible eliminar la posición porque tiene registros relacionados.",
                status=409,
            )
        return JsonResponse({"status": "deleted"})


class RestPeriodCollectionView(StaffRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> JsonResponse:
        payload, error = _load_json_body(request)
        if error:
            return error

        form = OperatorRestPeriodForm(payload)
        if not form.is_valid():
            return _json_error("Datos inválidos para el descanso.", errors=_form_errors(form))

        rest_period: OperatorRestPeriod = form.save(commit=False)
        if not rest_period.created_by_id:
            rest_period.created_by = request.user
        rest_period.save()

        removed_assignment_count, affected_calendar_ids = _release_assignments_for_rest_period(rest_period)
        if rest_period.calendar_id:
            affected_calendar_ids.add(rest_period.calendar_id)

        rest_period = (
            OperatorRestPeriod.objects.select_related("calendar", "created_by", "operator")
            .get(pk=rest_period.pk)
        )

        if affected_calendar_ids:
            calendars = ShiftCalendar.objects.filter(pk__in=affected_calendar_ids)
            for calendar in calendars:
                _refresh_workload_snapshots(calendar)

        requires_refresh = _rest_period_requires_calendar_refresh(
            rest_period.operator_id,
            rest_period.start_date,
            rest_period.end_date,
            rest_period.source,
        )

        return JsonResponse(
            {
                "rest_period": _rest_period_payload(rest_period),
                "requires_calendar_refresh": bool(removed_assignment_count) or requires_refresh,
                "removed_assignments": removed_assignment_count,
            },
            status=201,
        )


class RestPeriodDetailView(StaffRequiredMixin, View):
    http_method_names = ["patch", "delete"]

    def patch(self, request: HttpRequest, rest_period_id: int, *args: Any, **kwargs: Any) -> JsonResponse:
        rest_period = get_object_or_404(OperatorRestPeriod, pk=rest_period_id)
        payload, error = _load_json_body(request)
        if error:
            return error

        form_data = {
            "operator": rest_period.operator_id,
            "start_date": rest_period.start_date,
            "end_date": rest_period.end_date,
            "status": rest_period.status,
            "source": rest_period.source,
            "calendar": rest_period.calendar_id,
            "notes": rest_period.notes,
        }
        for key, value in payload.items():
            form_data[key] = value

        form = OperatorRestPeriodForm(form_data, instance=rest_period)
        if not form.is_valid():
            return _json_error("Datos inválidos para el descanso.", errors=_form_errors(form))

        rest_period = form.save()
        rest_period = (
            OperatorRestPeriod.objects.select_related("calendar", "created_by", "operator")
            .get(pk=rest_period.pk)
        )

        removed_assignment_count, affected_calendar_ids = _release_assignments_for_rest_period(rest_period)
        if rest_period.calendar_id:
            affected_calendar_ids.add(rest_period.calendar_id)

        if affected_calendar_ids:
            calendars = ShiftCalendar.objects.filter(pk__in=affected_calendar_ids)
            for calendar in calendars:
                _refresh_workload_snapshots(calendar)

        requires_refresh = _rest_period_requires_calendar_refresh(
            rest_period.operator_id,
            rest_period.start_date,
            rest_period.end_date,
            rest_period.source,
        )

        return JsonResponse(
            {
                "rest_period": _rest_period_payload(rest_period),
                "requires_calendar_refresh": bool(removed_assignment_count) or requires_refresh,
                "removed_assignments": removed_assignment_count,
            }
        )

    def delete(self, request: HttpRequest, rest_period_id: int, *args: Any, **kwargs: Any) -> JsonResponse:
        rest_period = get_object_or_404(OperatorRestPeriod, pk=rest_period_id)
        requires_refresh = _rest_period_requires_calendar_refresh(
            rest_period.operator_id,
            rest_period.start_date,
            rest_period.end_date,
            rest_period.source,
        )
        rest_period.delete()
        return JsonResponse(
            {
                "status": "deleted",
                "requires_calendar_refresh": requires_refresh,
            }
        )


class CalendarApproveView(StaffRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, calendar_id: int, *args: Any, **kwargs: Any) -> JsonResponse:
        try:
            calendar = ShiftCalendar.objects.get(pk=calendar_id)
        except ShiftCalendar.DoesNotExist:
            return HttpResponseBadRequest("Calendario no encontrado")

        calendar.mark_approved(request.user)

        return JsonResponse(
            {
                "calendar_id": calendar.id,
                "status": calendar.status,
                "approved_at": calendar.approved_at.isoformat() if calendar.approved_at else None,
            }
        )


class CalendarListView(StaffRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> JsonResponse:
        calendars = (
            ShiftCalendar.objects.select_related("created_by", "approved_by", "base_calendar")
            .order_by("-start_date", "-created_at")
        )
        response = [
            {
                "id": calendar.id,
                "name": calendar.name,
                "start_date": calendar.start_date.isoformat(),
                "end_date": calendar.end_date.isoformat(),
                "status": calendar.status,
                "base_calendar_id": calendar.base_calendar_id,
                "created_by": calendar.created_by_id,
                "approved_by": calendar.approved_by_id,
            }
            for calendar in calendars
        ]

        return JsonResponse({"results": response})


class CalendarMetadataView(StaffRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> JsonResponse:
        include_inactive = request.GET.get("include_inactive") == "true"
        farm_filter = request.GET.get("farm")
        reference_date = UserProfile.colombia_today()

        position_qs = PositionDefinition.objects.select_related("farm", "chicken_house", "category", "handoff_position").prefetch_related("rooms")
        if farm_filter:
            try:
                position_qs = position_qs.filter(farm_id=int(farm_filter))
            except (TypeError, ValueError):  # pragma: no cover - defensive casting
                return HttpResponseBadRequest("Identificador de granja inválido.")
        if not include_inactive:
            position_qs = position_qs.active_on(reference_date)

        positions_payload = [
            _position_payload(position, reference_date=reference_date)
            for position in position_qs.order_by("display_order", "id")
        ]

        rest_period_qs = (
            OperatorRestPeriod.objects.select_related("operator", "calendar", "created_by")
            .order_by("-start_date", "-created_at")
        )
        rest_periods_payload = [_rest_period_payload(period) for period in rest_period_qs]

        farms_payload = [
            {
                "id": farm.id,
                "name": farm.name,
            }
            for farm in Farm.objects.order_by("name")
        ]

        chicken_houses_payload = [
            {
                "id": house.id,
                "name": house.name,
                "farm_id": house.farm_id,
            }
            for house in ChickenHouse.objects.select_related("farm").order_by("farm__name", "name")
        ]

        rooms_payload = [
            {
                "id": room.id,
                "name": room.name,
                "chicken_house_id": room.chicken_house_id,
                "farm_id": room.chicken_house.farm_id,
            }
            for room in Room.objects.select_related("chicken_house", "chicken_house__farm").order_by("chicken_house__farm__name", "chicken_house__name", "name")
        ]

        roles_payload = [
            {
                "id": role.id,
                "code": role.name,
                "label": role.get_name_display(),
            }
            for role in Role.objects.order_by("name")
        ]

        category_qs = PositionCategory.objects.order_by("code")
        categories_payload = [
            {
                "id": category.id,
                "code": category.code,
                "name": category.display_name,
                "shift_type": category.shift_type,
                "rest_max_consecutive_days": category.rest_max_consecutive_days,
                "rest_post_shift_days": category.rest_post_shift_days,
                "rest_monthly_days": category.rest_monthly_days,
                "is_active": category.is_active,
            }
            for category in category_qs
        ]

        reference_date = UserProfile.colombia_today()

        operator_qs = UserProfile.objects.prefetch_related("roles", "suggested_positions").order_by(
            "apellidos",
            "nombres",
        )
        operators_payload = [
            _operator_payload(operator, roles=list(operator.roles.all()), reference_date=reference_date)
            for operator in operator_qs
        ]

        response_payload: Dict[str, Any] = {
            "positions": positions_payload,
            "operators": operators_payload,
            "rest_periods": rest_periods_payload,
            "categories": categories_payload,
            "farms": farms_payload,
            "chicken_houses": chicken_houses_payload,
            "rooms": rooms_payload,
            "roles": roles_payload,
            "choice_sets": {
                "position_categories": [
                    {
                        "value": str(category["id"]),
                        "label": category["name"],
                        "code": category["code"],
                        "shift_type": category["shift_type"],
                        "rest_max_consecutive_days": category["rest_max_consecutive_days"],
                        "rest_post_shift_days": category["rest_post_shift_days"],
                        "rest_monthly_days": category["rest_monthly_days"],
                    }
                    for category in categories_payload
                ],
                "shift_types": _choice_payload(ShiftType.choices),
                "alert_levels": _choice_payload(AssignmentAlertLevel.choices),
                "calendar_status": _choice_payload(CalendarStatus.choices),
                "days_of_week": _choice_payload(DayOfWeek.choices),
                "rest_statuses": _choice_payload(RestPeriodStatus.choices),
                "rest_sources": _choice_payload(RestPeriodSource.choices),
            },
        }

        return JsonResponse(response_payload)


class CalendarSummaryView(StaffRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, calendar_id: int, *args: Any, **kwargs: Any) -> JsonResponse:
        calendar = get_object_or_404(
            ShiftCalendar.objects.select_related("created_by", "approved_by", "base_calendar"),
            pk=calendar_id,
        )

        date_columns, rows, rest_rows, rest_summary, position_groups = _build_assignment_matrix(calendar)
        stats = _calculate_stats(rows)
        assignment_issues = _identify_calendar_issues(date_columns, rows, rest_rows)

        response_payload = {
            "calendar": {
                "id": calendar.id,
                "name": calendar.name,
                "status": calendar.status,
                "start_date": calendar.start_date.isoformat(),
                "end_date": calendar.end_date.isoformat(),
                "notes": calendar.notes,
            },
            "dates": [column.isoformat() for column in date_columns],
            "rows": [
                {
                    "position": _position_payload(row["position"]),
                    "cells": [
                        {
                            "date": cell["date"].isoformat(),
                            "is_position_active": cell["is_position_active"],
                            "assignment": _assignment_payload(cell["assignment"]),
                            "alert": cell["alert"],
                            "choices": cell["choices"],
                            "skill_gap_message": cell["skill_gap_message"],
                            "is_overtime": cell["is_overtime"],
                            "overtime_message": cell["overtime_message"],
                        }
                        for cell in row["cells"]
                    ],
                }
                for row in rows
            ],
            "rest_rows": [
                {
                    "label": rest_row["label"],
                    "slot": rest_row["slot"],
                    "operator_id": rest_row.get("operator_id"),
                    "has_rest": rest_row.get("has_rest", False),
                    "has_unassigned": rest_row.get("has_unassigned", False),
                    "cells": [
                        {
                            "operator_id": cell["operator_id"],
                            "name": cell["name"],
                            "role": cell["role"],
                            "reason": cell["reason"],
                            "state": cell.get("state"),
                            "status_label": cell.get("status_label"),
                        }
                        for cell in rest_row["cells"]
                    ],
                }
                for rest_row in rest_rows
            ],
            "position_groups": position_groups,
            "rest_summary": rest_summary,
            "stats": stats,
            "issues": assignment_issues,
        }

        return JsonResponse(response_payload)


class CalendarAssignmentCollectionView(StaffRequiredMixin, View):
    http_method_names = ["get", "post"]

    def get(self, request: HttpRequest, calendar_id: int, *args: Any, **kwargs: Any) -> JsonResponse:
        calendar = get_object_or_404(
            ShiftCalendar.objects.select_related("created_by", "approved_by"),
            pk=calendar_id,
        )
        assignments = (
            calendar.assignments.select_related("position", "position__farm", "operator")
            .prefetch_related("operator__roles", "operator__suggested_positions")
            .order_by("date", "position__display_order", "position__code")
        )

        payload = [_assignment_payload(assignment) for assignment in assignments]

        return JsonResponse({
            "calendar": {
                "id": calendar.id,
                "name": calendar.name,
                "status": calendar.status,
                "start_date": calendar.start_date.isoformat(),
                "end_date": calendar.end_date.isoformat(),
            },
            "assignments": payload,
        })

    def post(self, request: HttpRequest, calendar_id: int, *args: Any, **kwargs: Any) -> JsonResponse:
        calendar = get_object_or_404(ShiftCalendar, pk=calendar_id)

        try:
            payload = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return HttpResponseBadRequest("JSON inválido")

        position_id = payload.get("position_id")
        operator_id = payload.get("operator_id")
        target_date_raw = payload.get("date")

        if not position_id or not target_date_raw:
            return HttpResponseBadRequest("Campos 'position_id' y 'date' son obligatorios.")

        try:
            target_date = _parse_date(target_date_raw, "date")
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        position = get_object_or_404(PositionDefinition, pk=position_id)

        if calendar.status == CalendarStatus.APPROVED and target_date < timezone.localdate():
            return HttpResponseBadRequest(
                "No es posible modificar asignaciones de fechas pasadas en un calendario aprobado."
            )

        if not (calendar.start_date <= target_date <= calendar.end_date):
            return HttpResponseBadRequest("La fecha indicada no pertenece al personal.")

        if not position.is_active_on(target_date):
            return HttpResponseBadRequest("La posición no está vigente para la fecha seleccionada.")

        if not operator_id:
            return HttpResponseBadRequest("Debes seleccionar un colaborador para la asignación.")

        operator = get_object_or_404(UserProfile.objects.prefetch_related("roles"), pk=operator_id)

        alert_level = payload.get("alert_level", AssignmentAlertLevel.NONE)
        valid_alerts = {choice[0] for choice in AssignmentAlertLevel.choices}
        if alert_level not in valid_alerts:
            return HttpResponseBadRequest("Nivel de alerta inválido.")

        is_overtime = bool(payload.get("is_overtime", False))
        overtime_points = 0
        if is_overtime:
            policy = resolve_overload_policy(position.category)
            overtime_points = policy.overtime_points

        assignment, _created = ShiftAssignment.objects.update_or_create(
            calendar=calendar,
            position=position,
            date=target_date,
            defaults={
                "operator": operator,
                "alert_level": alert_level,
                "is_auto_assigned": False,
                "is_overtime": is_overtime,
                "overtime_points": overtime_points if is_overtime else 0,
                "notes": payload.get("notes", ""),
            },
        )

        _refresh_workload_snapshots(calendar)

        assignment = (
            ShiftAssignment.objects.select_related("position", "position__farm", "operator")
            .prefetch_related("operator__roles", "operator__suggested_positions")
            .get(pk=assignment.pk)
        )

        return JsonResponse({"assignment": _assignment_payload(assignment)}, status=201)


class CalendarAssignmentDetailView(StaffRequiredMixin, View):
    http_method_names = ["delete"]

    def delete(self, request: HttpRequest, calendar_id: int, assignment_id: int, *args: Any, **kwargs: Any) -> JsonResponse:
        calendar = get_object_or_404(ShiftCalendar, pk=calendar_id)
        assignment = get_object_or_404(
            ShiftAssignment.objects.select_related("calendar"),
            pk=assignment_id,
            calendar=calendar,
        )

        if calendar.status == CalendarStatus.APPROVED and assignment.date < timezone.localdate():
            return HttpResponseBadRequest(
                "No es posible retirar asignaciones históricas de un calendario aprobado."
            )

        assignment.delete()
        _refresh_workload_snapshots(calendar)

        return JsonResponse({"status": "deleted"})


class CalendarEligibleOperatorsView(StaffRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, calendar_id: int, *args: Any, **kwargs: Any) -> JsonResponse:
        calendar = get_object_or_404(ShiftCalendar, pk=calendar_id)
        position_id = request.GET.get("position")
        date_raw = request.GET.get("date")

        if not position_id or not date_raw:
            return HttpResponseBadRequest("Se requieren los parámetros 'position' y 'date'.")

        try:
            position = PositionDefinition.objects.get(pk=int(position_id))
        except (PositionDefinition.DoesNotExist, ValueError):
            return HttpResponseBadRequest("Posición no encontrada.")

        try:
            target_date = _parse_date(date_raw, "date")
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))

        assignments = list(
            calendar.assignments.select_related("position", "operator")
            .prefetch_related("operator__roles", "operator__suggested_positions")
        )

        choices_map = _eligible_operator_map(calendar, [position], [target_date], assignments)
        choices = choices_map.get(position.id, {}).get(target_date, [])

        return JsonResponse({"results": choices})

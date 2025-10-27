from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

from django import forms
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError, Q
from django.http import HttpRequest, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views import View

from .forms import (
    AssignmentCreateForm,
    AssignmentUpdateForm,
    CalendarGenerationForm,
    OperatorCapabilityForm,
    OperatorProfileForm,
    PositionDefinitionForm,
)
from .models import (
    AssignmentAlertLevel,
    DayOfWeek,
    CalendarStatus,
    OperatorCapability,
    PositionCategory,
    PositionDefinition,
    OperatorRestPeriod,
    RestPeriodSource,
    RestPeriodStatus,
    ShiftAssignment,
    ShiftCalendar,
    ShiftType,
    resolve_overload_policy,
    required_skill_for_complexity,
)
from .services import CalendarScheduler, SchedulerOptions
from granjas.models import ChickenHouse, Farm, Room
from users.models import Role, UserProfile


def _parse_date(value: str, field_name: str) -> date:
    parsed = parse_date(value)
    if not parsed:
        raise ValueError(f"Formato de fecha inválido para {field_name}.")
    return parsed


def _date_range(start: date, end: date) -> List[date]:
    days = (end - start).days
    return [start + timedelta(days=offset) for offset in range(days + 1)]


def _build_assignment_matrix(
    calendar: ShiftCalendar,
) -> tuple[List[date], List[dict[str, Any]], List[dict[str, Any]]]:
    date_columns = _date_range(calendar.start_date, calendar.end_date)
    assignment_list = list(
        calendar.assignments.select_related("position", "position__farm", "operator")
        .prefetch_related("operator__roles")
    )
    assignments = {
        (assignment.position_id, assignment.date): assignment for assignment in assignment_list
    }
    assigned_position_ids = {assignment.position_id for assignment in assignment_list}
    farm_ids = {
        assignment.position.farm_id
        for assignment in assignment_list
        if assignment.position and assignment.position.farm_id
    }

    position_filters = Q(valid_from__lte=calendar.end_date) & (
        Q(valid_until__isnull=True) | Q(valid_until__gte=calendar.start_date)
    )
    active_query = PositionDefinition.objects.filter(position_filters & Q(is_active=True))
    if farm_ids:
        active_query = active_query.filter(farm_id__in=farm_ids)
    positions = list(active_query.order_by("display_order", "id"))

    if assigned_position_ids:
        assigned_positions = list(
            PositionDefinition.objects.filter(id__in=assigned_position_ids).order_by(
                "display_order", "id"
            )
        )
        known_ids = {position.id for position in positions}
        for position in assigned_positions:
            if position.id not in known_ids:
                positions.append(position)
    positions.sort(key=lambda item: (item.display_order, item.id))

    eligible_map = _eligible_operator_map(calendar, positions, date_columns, assignment_list)

    rows: List[dict[str, Any]] = []
    for position in positions:
        cells: List[dict[str, Any]] = []
        for day in date_columns:
            is_position_active = position.is_active_on(day)
            assignment = assignments.get((position.id, day))
            choices = (
                eligible_map.get(position.id, {}).get(day, [])
                if is_position_active
                else []
            )
            cells.append(
                {
                    "assignment": assignment,
                    "alert": assignment.alert_level if assignment else None,
                    "is_overtime": assignment.is_overtime if assignment else False,
                    "choices": choices,
                    "date": day,
                    "is_position_active": is_position_active,
                }
            )
        rows.append({"position": position, "cells": cells})

    rest_rows = _build_rest_rows(date_columns, assignment_list)

    return date_columns, rows, rest_rows


def _build_rest_rows(
    date_columns: Iterable[date],
    assignment_list: Iterable[ShiftAssignment],
) -> List[dict[str, Any]]:
    day_assignments: dict[date, set[int]] = defaultdict(set)
    operators: dict[int, UserProfile] = {}

    for assignment in assignment_list:
        if not assignment.operator_id:
            continue
        day_assignments[assignment.date].add(assignment.operator_id)
        operators[assignment.operator_id] = assignment.operator

    if not operators:
        return []

    sorted_operators = sorted(
        operators.values(),
        key=lambda operator: (
            (operator.apellidos or "").lower(),
            (operator.nombres or "").lower(),
            operator.id,
        ),
    )

    rest_matrix: dict[date, list[UserProfile]] = {}
    for day in date_columns:
        working_ids = day_assignments.get(day, set())
        resters = [op for op in sorted_operators if op.id not in working_ids]
        rest_matrix[day] = resters

    max_slots = max((len(resters) for resters in rest_matrix.values()), default=0)
    if max_slots == 0:
        return []

    rest_rows: List[dict[str, Any]] = []
    for slot_index in range(max_slots):
        cells: List[dict[str, Any]] = []
        for day in date_columns:
            resters = rest_matrix.get(day, [])
            if slot_index < len(resters):
                operator = resters[slot_index]
                roles = list(operator.roles.all())
                role_label = roles[0].get_name_display() if roles else ""
                display_name = (
                    operator.get_full_name()
                    or operator.nombres
                    or operator.apellidos
                    or operator.cedula
                )
                cells.append(
                    {
                        "operator_id": operator.id,
                        "name": display_name,
                        "role": role_label,
                    }
                )
            else:
                cells.append({"operator_id": None, "name": "", "role": ""})

        rest_rows.append(
            {
                "label": "Descansos",
                "slot": slot_index + 1,
                "cells": cells,
            }
        )

    return rest_rows


def _serialize_rest_period(period: OperatorRestPeriod) -> dict[str, Any]:
    return {
        "start": period.start_date.isoformat(),
        "end": period.end_date.isoformat(),
        "status": period.status,
        "status_label": RestPeriodStatus(period.status).label,
        "source": period.source,
        "source_label": RestPeriodSource(period.source).label,
    }


def _build_rest_summary(
    calendar: ShiftCalendar,
    operator_map: dict[int, UserProfile],
) -> dict[str, Any]:
    if not operator_map:
        return {}

    operator_ids = list(operator_map.keys())
    periods = (
        OperatorRestPeriod.objects.filter(operator_id__in=operator_ids)
        .exclude(status=RestPeriodStatus.CANCELLED)
        .order_by("start_date", "end_date")
    )

    periods_by_operator: dict[int, list[OperatorRestPeriod]] = defaultdict(list)
    for period in periods:
        periods_by_operator[period.operator_id].append(period)

    summary: dict[str, Any] = {}
    for operator_id, operator in operator_map.items():
        operator_periods = periods_by_operator.get(operator_id, [])
        latest_completed: Optional[OperatorRestPeriod] = None
        upcoming: Optional[OperatorRestPeriod] = None
        current: Optional[OperatorRestPeriod] = None

        for period in operator_periods:
            if period.start_date <= calendar.start_date <= period.end_date:
                if current is None or period.end_date > current.end_date:
                    current = period
            if period.end_date < calendar.start_date:
                if latest_completed is None or period.end_date > latest_completed.end_date:
                    latest_completed = period
            elif period.start_date >= calendar.start_date:
                if upcoming is None or period.start_date < upcoming.start_date:
                    upcoming = period

        display_name = operator.get_full_name() or operator.nombres or operator.apellidos or ""

        summary[str(operator_id)] = {
            "name": display_name,
            "employment_start": operator.employment_start_date.isoformat()
            if operator.employment_start_date
            else None,
            "current": _serialize_rest_period(current) if current else None,
            "recent": _serialize_rest_period(latest_completed) if latest_completed else None,
            "upcoming": _serialize_rest_period(upcoming) if upcoming else None,
        }

    return summary


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

    category_ids = {position.category_id for position in positions}
    if not category_ids:
        return {}

    assignment_by_operator_day: dict[tuple[int, date], List[Any]] = defaultdict(list)
    for assignment in assignment_list:
        if assignment.operator_id:
            assignment_by_operator_day[(assignment.operator_id, assignment.date)].append(
                assignment
            )

    start = min(date_columns)
    end = max(date_columns)

    capabilities_qs = (
        OperatorCapability.objects.select_related("operator", "category")
        .prefetch_related("operator__roles")
        .filter(category_id__in=category_ids)
    )

    capability_index: dict[tuple[int, int], OperatorCapability] = {}
    operators_by_category: dict[int, set[int]] = defaultdict(set)
    operator_cache: dict[int, Any] = {}

    for capability in capabilities_qs:
        operator = capability.operator
        operator_cache[operator.id] = operator
        capability_index[(capability.category_id, operator.id)] = capability
        operators_by_category[capability.category_id].add(operator.id)

    result: dict[int, dict[date, List[dict[str, Any]]]] = defaultdict(dict)

    for position in positions:
        operator_ids = operators_by_category.get(position.category_id, set())
        required_score = required_skill_for_complexity(position.complexity)
        for day in date_columns:
            if not position.is_active_on(day):
                result[position.id][day] = []
                continue

            if not operator_ids:
                result[position.id][day] = []
                continue

            choices: List[dict[str, Any]] = []
            for operator_id in operator_ids:
                capability = capability_index.get((position.category_id, operator_id))
                if not capability:
                    continue

                skill_score = capability.skill_score
                if skill_score < required_score and not position.allow_lower_complexity:
                    continue

                alert = AssignmentAlertLevel.NONE
                if skill_score < required_score:
                    diff = required_score - skill_score
                    alert = (
                        AssignmentAlertLevel.WARN
                        if diff == 1
                        else AssignmentAlertLevel.CRITICAL
                    )

                busy_assignments = assignment_by_operator_day.get((operator_id, day), [])
                disabled = any(assign.position_id != position.id for assign in busy_assignments)

                operator = operator_cache[operator_id]
                roles = list(operator.roles.all())
                role_label = roles[0].get_name_display() if roles else ""
                label = operator.get_full_name() or operator.nombres
                if role_label:
                    label = f"{label} · {role_label}"

                choices.append(
                    {
                        "id": operator_id,
                        "label": label,
                        "alert": alert,
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
            if not assignment:
                stats["gaps"] += 1
                continue

            stats["total_assignments"] += 1
            if assignment.alert_level == AssignmentAlertLevel.WARN:
                stats["warn"] += 1
            if assignment.alert_level == AssignmentAlertLevel.CRITICAL:
                stats["critical"] += 1
            if assignment.is_overtime:
                stats["overtime"] += 1

    return stats


def _refresh_workload_snapshots(calendar: ShiftCalendar) -> None:
    scheduler = CalendarScheduler(calendar, options=SchedulerOptions())
    scheduler._rebuild_workload_snapshots()
    scheduler.sync_rest_periods()


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


def _position_payload(position: PositionDefinition) -> dict[str, Any]:
    return {
        "id": position.id,
        "name": position.name,
        "code": position.code,
        "display_order": position.display_order,
        "category": {
            "id": position.category_id,
            "code": position.category.code,
            "name": position.category.name,
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
        "complexity": position.complexity,
        "allow_lower_complexity": position.allow_lower_complexity,
        "valid_from": position.valid_from.isoformat(),
        "valid_until": position.valid_until.isoformat() if position.valid_until else None,
        "is_active": position.is_active,
        "notes": position.notes,
    }


def _operator_payload(
    operator: UserProfile,
    *,
    roles: Optional[Iterable[Role]] = None,
) -> dict[str, Any]:
    role_items = roles if roles is not None else list(operator.roles.all())
    preferred_farm = getattr(operator, "preferred_farm", None)
    return {
        "id": operator.id,
        "name": operator.get_full_name() or operator.nombres,
        "document": operator.cedula,
        "nombres": operator.nombres,
        "apellidos": operator.apellidos,
        "telefono": operator.telefono,
        "email": operator.email,
        "preferred_farm_id": operator.preferred_farm_id,
        "preferred_farm": (
            {
                "id": operator.preferred_farm_id,
                "name": preferred_farm.name,
            }
            if operator.preferred_farm_id and preferred_farm
            else None
        ),
        "employment_start": operator.employment_start_date.isoformat()
        if operator.employment_start_date
        else None,
        "roles": [
            {
                "id": role.id,
                "code": role.name,
                "label": role.get_name_display(),
            }
            for role in role_items
        ],
        "is_active": operator.is_active,
    }


def _format_operator_label(operator: UserProfile) -> str:
    roles = list(operator.roles.all())
    role_label = roles[0].get_name_display() if roles else ""
    label = operator.get_full_name() or operator.nombres
    if role_label:
        label = f"{label} · {role_label}"
    return label

def _capability_payload(capability: OperatorCapability) -> dict[str, Any]:
    return {
        "id": capability.id,
        "operator_id": capability.operator_id,
        "category": {
            "id": capability.category_id,
            "code": capability.category.code,
            "name": capability.category.name,
            "shift_type": capability.category.shift_type,
        },
        "category_id": str(capability.category_id) if capability.category_id else "",
        "skill_score": capability.skill_score,
    }


def _normalize_capability_entries(data: Any) -> Tuple[Optional[dict[int, int]], dict[str, List[str]]]:
    if data is None:
        return None, {}

    if not isinstance(data, list):
        return None, {"capabilities": ["Formato inválido para las fortalezas."]}

    normalized: dict[int, int] = {}
    errors: dict[str, List[str]] = {}

    for index, entry in enumerate(data):
        prefix = f"capabilities[{index}]"
        if not isinstance(entry, dict):
            errors.setdefault(prefix, []).append("Formato inválido.")
            continue

        category_value = entry.get("category")
        skill_value_raw = entry.get("skill_score")
        entry_errors = False

        if category_value in (None, ""):
            errors.setdefault(f"{prefix}.category", []).append("Selecciona la categoría.")
            entry_errors = True
        else:
            try:
                category_id = int(category_value)
            except (TypeError, ValueError):
                errors.setdefault(f"{prefix}.category", []).append("Categoría inválida.")
                entry_errors = True
            else:
                if category_id in normalized:
                    errors.setdefault(f"{prefix}.category", []).append("La categoría ya fue registrada.")
                    entry_errors = True
                elif not PositionCategory.objects.filter(pk=category_id).exists():
                    errors.setdefault(f"{prefix}.category", []).append("Categoría inválida.")
                    entry_errors = True

        try:
            skill_value = int(skill_value_raw)
        except (TypeError, ValueError):
            errors.setdefault(f"{prefix}.skill_score", []).append("El nivel debe ser numérico.")
            entry_errors = True
        else:
            if not 1 <= skill_value <= 10:
                errors.setdefault(f"{prefix}.skill_score", []).append("El nivel debe estar entre 1 y 10.")
                entry_errors = True

        if not entry_errors:
            normalized[category_id] = skill_value

    return normalized, errors


def _apply_capabilities(operator: UserProfile, capabilities: Optional[dict[int, int]]) -> None:
    if capabilities is None:
        return

    existing = {cap.category_id: cap for cap in operator.capabilities.select_related("category")}
    desired_categories = set(capabilities.keys())
    category_map = PositionCategory.objects.in_bulk(desired_categories)

    for category, score in capabilities.items():
        category_obj = category_map.get(category)
        if not category_obj:
            continue
        capability = existing.get(category)
        if capability:
            if capability.skill_score != score:
                capability.skill_score = score
                capability.save(update_fields=["skill_score"])
        else:
            OperatorCapability.objects.create(
                operator=operator,
                category=category_obj,
                skill_score=score,
            )

    for category, capability in existing.items():
        if category not in desired_categories:
            capability.delete()


def _assignment_payload(assignment: Optional[ShiftAssignment]) -> Optional[dict[str, Any]]:
    if assignment is None:
        return None

    operator_payload: Optional[dict[str, Any]] = None
    if assignment.operator_id:
        roles = list(assignment.operator.roles.all()) if assignment.operator_id else []
        operator_payload = _operator_payload(assignment.operator, roles=roles) if assignment.operator_id else None

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


class CalendarConfiguratorView(LoginRequiredMixin, View):
    template_name = "calendario/configurator.html"

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> Any:
        return render(
            request,
            self.template_name,
            {
                "alert_choices": _choice_payload(AssignmentAlertLevel.choices),
                "shift_type_choices": _choice_payload(ShiftType.choices),
                "status_choices": _choice_payload(CalendarStatus.choices),
            },
        )


class CalendarDashboardView(LoginRequiredMixin, View):
    template_name = "calendario/dashboard.html"

    @staticmethod
    def _get_calendars() -> List[ShiftCalendar]:
        queryset = ShiftCalendar.objects.select_related(
            "created_by",
            "approved_by",
            "base_calendar",
        ).order_by("-start_date", "-created_at")
        return list(queryset)

    @staticmethod
    def _status_totals(calendars: Iterable[ShiftCalendar]) -> dict[str, int]:
        totals = {status: 0 for status, _ in CalendarStatus.choices}
        for calendar in calendars:
            totals[calendar.status] = totals.get(calendar.status, 0) + 1
        return totals

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> Any:
        form = CalendarGenerationForm()
        calendars = self._get_calendars()
        status_totals = self._status_totals(calendars)

        return render(
            request,
            self.template_name,
            {
                "form": form,
                "calendars": calendars,
                "status_totals": status_totals,
            },
        )

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> Any:
        form = CalendarGenerationForm(request.POST)
        calendars = self._get_calendars()
        status_totals = self._status_totals(calendars)

        if not form.is_valid():
            messages.error(request, "No fue posible generar el calendario. Revisa los datos ingresados.")
            return render(
                request,
                self.template_name,
                {
                    "form": form,
                    "calendars": calendars,
                    "status_totals": status_totals,
                },
            )

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

        return redirect(reverse("calendario:calendar-detail", args=[calendar.id]))

class CalendarDetailView(LoginRequiredMixin, View):
    template_name = "calendario/calendar_detail.html"

    def get(self, request: HttpRequest, pk: int, *args: Any, **kwargs: Any) -> Any:
        calendar = get_object_or_404(
            ShiftCalendar.objects.select_related("created_by", "approved_by", "base_calendar"),
            pk=pk,
        )
        date_columns, rows, rest_rows = _build_assignment_matrix(calendar)
        stats = _calculate_stats(rows)
        can_override = calendar.status in {CalendarStatus.DRAFT, CalendarStatus.MODIFIED}

        operator_map: dict[int, UserProfile] = {}
        for row in rows:
            for cell in row["cells"]:
                assignment = cell.get("assignment")
                if assignment and assignment.operator_id:
                    operator_map[assignment.operator_id] = assignment.operator

        rest_summary = _build_rest_summary(calendar, operator_map)

        manual_operator_choices: list[dict[str, Any]] = []
        if can_override:
            operator_qs = (
                UserProfile.objects.filter(is_active=True)
                .prefetch_related("roles")
                .order_by("apellidos", "nombres")
            )
            manual_operator_choices = [
                {
                    "id": operator.id,
                    "label": _format_operator_label(operator),
                }
                for operator in operator_qs
            ]

            known_ids = {item["id"] for item in manual_operator_choices}
            missing_ids: set[int] = set()
            for row in rows:
                for cell in row["cells"]:
                    assignment = cell.get("assignment")
                    if assignment and assignment.operator_id and assignment.operator_id not in known_ids:
                        missing_ids.add(assignment.operator_id)

            if missing_ids:
                extra_qs = (
                    UserProfile.objects.filter(id__in=missing_ids)
                    .prefetch_related("roles")
                    .order_by("apellidos", "nombres")
                )
                manual_operator_choices.extend(
                    {
                        "id": operator.id,
                        "label": _format_operator_label(operator),
                    }
                    for operator in extra_qs
                )

        return render(
            request,
            self.template_name,
            {
                "calendar": calendar,
                "date_columns": date_columns,
                "rows": rows,
                "stats": stats,
                "rest_rows": rest_rows,
                "rest_summary": rest_summary,
                "can_override": can_override,
                "manual_operator_choices": manual_operator_choices,
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
            form = AssignmentUpdateForm(request.POST, calendar=calendar)
            if form.is_valid():
                assignment: ShiftAssignment = form.cleaned_data["assignment"]
                operator: UserProfile = form.cleaned_data["operator"]
                alert_level: AssignmentAlertLevel = form.cleaned_data["alert_level"]
                is_overtime: bool = form.cleaned_data["is_overtime"]
                overtime_points: int = form.cleaned_data["overtime_points"]

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
                messages.success(request, "Asignación actualizada correctamente.")
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
                messages.success(request, "Turno asignado manualmente.")
            else:
                messages.error(request, form.errors.as_text())
        elif action == "mark-modified":
            if calendar.status != CalendarStatus.APPROVED:
                messages.info(request, "Solo los calendarios aprobados pueden marcarse como modificados.")
            else:
                calendar.status = CalendarStatus.MODIFIED
                calendar.save(update_fields=["status", "updated_at"])
                messages.info(request, "El calendario ahora se encuentra en estado modificado y está listo para ajustes.")
        else:
            messages.error(request, "Acción no reconocida.")

        return redirect(reverse("calendario:calendar-detail", args=[calendar.id]))


class CalendarDeleteView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, pk: int, *args: Any, **kwargs: Any) -> Any:
        calendar = get_object_or_404(ShiftCalendar, pk=pk)
        redirect_url = request.POST.get("next") or reverse("calendario:dashboard")

        calendar_label = calendar.name or f"Calendario {calendar.start_date} -> {calendar.end_date}"

        try:
            calendar.delete()
        except ProtectedError:
            messages.error(
                request,
                "No es posible eliminar este calendario porque tiene modificaciones asociadas.",
            )
        else:
            messages.success(request, f'Se eliminó el calendario "{calendar_label}".')

        return redirect(redirect_url)


# ---------------------------------------------------------------------------
# API JSON
# ---------------------------------------------------------------------------


class CalendarGenerateView(LoginRequiredMixin, View):
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


class OperatorCollectionView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> JsonResponse:
        payload, error = _load_json_body(request)
        if error:
            return error

        form = OperatorProfileForm(payload)
        if not form.is_valid():
            return _json_error("Datos inválidos para el colaborador.", errors=_form_errors(form))

        capabilities_map, capability_errors = _normalize_capability_entries(payload.get("capabilities"))
        if capability_errors:
            return _json_error("Datos inválidos para las fortalezas.", errors=capability_errors)

        with transaction.atomic():
            operator = form.save()
            _apply_capabilities(operator, capabilities_map)

        operator = (
            UserProfile.objects.select_related("preferred_farm")
            .prefetch_related("roles")
            .get(pk=operator.pk)
        )
        return JsonResponse({"operator": _operator_payload(operator)}, status=201)


class OperatorDetailView(LoginRequiredMixin, View):
    http_method_names = ["patch"]

    def patch(self, request: HttpRequest, operator_id: int, *args: Any, **kwargs: Any) -> JsonResponse:
        operator = get_object_or_404(UserProfile, pk=operator_id)
        payload, error = _load_json_body(request)
        if error:
            return error

        # Merge current operator values with incoming payload so the form receives a complete dataset.
        form_fields = list(OperatorProfileForm._meta.fields)  # type: ignore[attr-defined]
        form_data: dict[str, Any] = {}
        for field_name in form_fields:
            if field_name == "roles":
                form_data[field_name] = list(operator.roles.values_list("pk", flat=True))
            elif field_name == "preferred_farm":
                form_data[field_name] = operator.preferred_farm_id
            else:
                form_data[field_name] = getattr(operator, field_name)

        for key, value in payload.items():
            if key in form_data:
                form_data[key] = value

        capabilities_map, capability_errors = _normalize_capability_entries(payload.get("capabilities"))
        if capability_errors:
            return _json_error("Datos inválidos para las fortalezas.", errors=capability_errors)

        form = OperatorProfileForm(form_data, instance=operator)
        if not form.is_valid():
            return _json_error("Datos inválidos para el colaborador.", errors=_form_errors(form))

        with transaction.atomic():
            operator = form.save()
            if capabilities_map is not None:
                _apply_capabilities(operator, capabilities_map)

        operator = (
            UserProfile.objects.select_related("preferred_farm")
            .prefetch_related("roles")
            .get(pk=operator.pk)
        )
        return JsonResponse({"operator": _operator_payload(operator)})


class PositionCollectionView(LoginRequiredMixin, View):
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


class PositionReorderView(LoginRequiredMixin, View):
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
            PositionDefinition.objects.select_related("farm", "chicken_house", "category")
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
            for position in PositionDefinition.objects.select_related("farm", "chicken_house", "category")
            .prefetch_related("rooms")
            .order_by("display_order", "id")
        ]
        return JsonResponse({"positions": refreshed_positions})


class PositionDetailView(LoginRequiredMixin, View):
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


class CapabilityCollectionView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> JsonResponse:
        payload, error = _load_json_body(request)
        if error:
            return error

        form = OperatorCapabilityForm(payload)
        if not form.is_valid():
            return _json_error("Datos inválidos para la fortaleza.", errors=_form_errors(form))

        capability = form.save()
        return JsonResponse({"capability": _capability_payload(capability)}, status=201)


class CapabilityDetailView(LoginRequiredMixin, View):
    http_method_names = ["patch", "delete"]

    def patch(self, request: HttpRequest, capability_id: int, *args: Any, **kwargs: Any) -> JsonResponse:
        capability = get_object_or_404(OperatorCapability, pk=capability_id)
        payload, error = _load_json_body(request)
        if error:
            return error

        form = OperatorCapabilityForm(payload, instance=capability)
        if not form.is_valid():
            return _json_error("Datos inválidos para la fortaleza.", errors=_form_errors(form))

        capability = form.save()
        return JsonResponse({"capability": _capability_payload(capability)})

    def delete(self, request: HttpRequest, capability_id: int, *args: Any, **kwargs: Any) -> JsonResponse:
        capability = get_object_or_404(OperatorCapability, pk=capability_id)
        capability.delete()
        return JsonResponse({"status": "deleted"})


class CalendarApproveView(LoginRequiredMixin, View):
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


class CalendarListView(LoginRequiredMixin, View):
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


class CalendarMetadataView(LoginRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> JsonResponse:
        include_inactive = request.GET.get("include_inactive") == "true"
        farm_filter = request.GET.get("farm")

        position_qs = PositionDefinition.objects.select_related("farm", "chicken_house", "category").prefetch_related("rooms")
        if farm_filter:
            try:
                position_qs = position_qs.filter(farm_id=int(farm_filter))
            except (TypeError, ValueError):  # pragma: no cover - defensive casting
                return HttpResponseBadRequest("Identificador de granja inválido.")
        if not include_inactive:
            position_qs = position_qs.filter(is_active=True)

        positions_payload = [
            _position_payload(position)
            for position in position_qs.order_by("display_order", "id")
        ]

        capability_qs = (
            OperatorCapability.objects.select_related("operator", "category")
            .prefetch_related("operator__roles")
            .order_by("operator__apellidos", "operator__nombres", "category__name")
        )
        capabilities_payload = [_capability_payload(capability) for capability in capability_qs]

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

        category_qs = PositionCategory.objects.order_by("name")
        categories_payload = [
            {
                "id": category.id,
                "code": category.code,
                "name": category.name,
                "shift_type": category.shift_type,
                "extra_day_limit": category.extra_day_limit,
                "overtime_points": category.overtime_points,
                "overload_alert_level": category.overload_alert_level,
                "rest_min_frequency": category.rest_min_frequency,
                "rest_min_consecutive_days": category.rest_min_consecutive_days,
                "rest_max_consecutive_days": category.rest_max_consecutive_days,
                "rest_post_shift_days": category.rest_post_shift_days,
                "rest_monthly_days": category.rest_monthly_days,
                "is_active": category.is_active,
            }
            for category in category_qs
        ]

        operator_qs = (
            UserProfile.objects.select_related("preferred_farm")
            .prefetch_related("roles")
            .order_by("apellidos", "nombres")
        )
        operators_payload = [
            _operator_payload(operator, roles=list(operator.roles.all()))
            for operator in operator_qs
        ]

        response_payload: Dict[str, Any] = {
            "positions": positions_payload,
            "operators": operators_payload,
            "capabilities": capabilities_payload,
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
                        "extra_day_limit": category["extra_day_limit"],
                        "overtime_points": category["overtime_points"],
                        "overload_alert_level": category["overload_alert_level"],
                        "rest_min_frequency": category["rest_min_frequency"],
                        "rest_min_consecutive_days": category["rest_min_consecutive_days"],
                        "rest_max_consecutive_days": category["rest_max_consecutive_days"],
                        "rest_post_shift_days": category["rest_post_shift_days"],
                        "rest_monthly_days": category["rest_monthly_days"],
                    }
                    for category in categories_payload
                ],
                "complexity_levels": _choice_payload(PositionDefinition._meta.get_field("complexity").choices),
                "skill_levels": [
                    {"value": str(score), "label": f"{score}/10"}
                    for score in range(1, 11)
                ],
                "shift_types": _choice_payload(ShiftType.choices),
                "alert_levels": _choice_payload(AssignmentAlertLevel.choices),
                "calendar_status": _choice_payload(CalendarStatus.choices),
                "days_of_week": _choice_payload(DayOfWeek.choices),
            },
        }

        return JsonResponse(response_payload)


class CalendarSummaryView(LoginRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, calendar_id: int, *args: Any, **kwargs: Any) -> JsonResponse:
        calendar = get_object_or_404(
            ShiftCalendar.objects.select_related("created_by", "approved_by", "base_calendar"),
            pk=calendar_id,
        )

        date_columns, rows, _rest_rows = _build_assignment_matrix(calendar)
        stats = _calculate_stats(rows)

        row_payload = []
        for row in rows:
            position = row["position"]
            cells_payload = []
            for cell in row["cells"]:
                cells_payload.append(
                    {
                        "date": cell["date"].isoformat(),
                        "assignment": _assignment_payload(cell["assignment"]),
                        "alert": cell["alert"],
                        "is_overtime": cell["is_overtime"],
                        "choices": cell.get("choices", []),
                        "is_position_active": cell.get("is_position_active", True),
                    }
                )

            row_payload.append(
                {
                    "position": _position_payload(position),
                    "cells": cells_payload,
                }
            )

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
            "rows": row_payload,
            "stats": stats,
        }

        return JsonResponse(response_payload)


class CalendarAssignmentCollectionView(LoginRequiredMixin, View):
    http_method_names = ["get", "post"]

    def get(self, request: HttpRequest, calendar_id: int, *args: Any, **kwargs: Any) -> JsonResponse:
        calendar = get_object_or_404(
            ShiftCalendar.objects.select_related("created_by", "approved_by"),
            pk=calendar_id,
        )
        assignments = (
            calendar.assignments.select_related("position", "position__farm", "operator")
            .prefetch_related("operator__roles")
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
            return HttpResponseBadRequest("La fecha indicada no pertenece al calendario.")

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
            .prefetch_related("operator__roles")
            .get(pk=assignment.pk)
        )

        return JsonResponse({"assignment": _assignment_payload(assignment)}, status=201)


class CalendarAssignmentDetailView(LoginRequiredMixin, View):
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


class CalendarEligibleOperatorsView(LoginRequiredMixin, View):
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
            .prefetch_related("operator__roles")
        )

        choices_map = _eligible_operator_map(calendar, [position], [target_date], assignments)
        choices = choices_map.get(position.id, {}).get(target_date, [])

        return JsonResponse({"results": choices})

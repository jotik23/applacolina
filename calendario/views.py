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
    OverloadAllowanceForm,
    PositionDefinitionForm,
    RestRuleForm,
)
from .models import (
    AssignmentAlertLevel,
    DayOfWeek,
    CalendarStatus,
    OverloadAllowance,
    OperatorCapability,
    PositionCategory,
    PositionDefinition,
    RestPreference,
    RestRule,
    ShiftAssignment,
    ShiftCalendar,
    ShiftType,
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


def _build_assignment_matrix(calendar: ShiftCalendar) -> tuple[List[date], List[dict[str, Any]]]:
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
    position_query = PositionDefinition.objects.filter(position_filters)
    if farm_ids:
        position_query = position_query.filter(farm_id__in=farm_ids)
    positions = list(position_query.order_by("display_order", "id"))

    if not positions and assigned_position_ids:
        positions = list(
            PositionDefinition.objects.filter(id__in=assigned_position_ids).order_by(
                "display_order", "id"
            )
        )
    elif not positions:
        positions = list(position_query.order_by("display_order", "id"))

    eligible_map = _eligible_operator_map(calendar, positions, date_columns, assignment_list)

    rows: List[dict[str, Any]] = []
    for position in positions:
        cells: List[dict[str, Any]] = []
        for day in date_columns:
            assignment = assignments.get((position.id, day))
            choices = eligible_map.get(position.id, {}).get(day, [])
            cells.append(
                {
                    "assignment": assignment,
                    "alert": assignment.alert_level if assignment else None,
                    "is_overtime": assignment.is_overtime if assignment else False,
                    "choices": choices,
                    "date": day,
                }
            )
        rows.append({"position": position, "cells": cells})

    return date_columns, rows


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

    categories = {position.category for position in positions}
    if not categories:
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
        OperatorCapability.objects.select_related("operator")
        .prefetch_related("operator__roles")
        .filter(category__in=categories)
    )

    capability_index: dict[tuple[str, int], OperatorCapability] = {}
    operators_by_category: dict[str, set[int]] = defaultdict(set)
    operator_cache: dict[int, Any] = {}

    for capability in capabilities_qs:
        operator = capability.operator
        operator_cache[operator.id] = operator
        capability_index[(capability.category, operator.id)] = capability
        operators_by_category[capability.category].add(operator.id)

    result: dict[int, dict[date, List[dict[str, Any]]]] = defaultdict(dict)

    for position in positions:
        operator_ids = operators_by_category.get(position.category, set())
        if not operator_ids:
            for day in date_columns:
                result[position.id][day] = []
            continue

        required_score = required_skill_for_complexity(position.complexity)
        for day in date_columns:
            choices: List[dict[str, Any]] = []
            for operator_id in operator_ids:
                capability = capability_index.get((position.category, operator_id))
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


def _sync_rest_preferences(rule: RestRule, preferences_payload: Iterable[dict[str, Any]]) -> None:
    rule.preferred_days.all().delete()
    days_seen: set[int] = set()
    bulk_create: list[RestPreference] = []

    for item in preferences_payload:
        try:
            day = int(item.get("day_of_week"))
        except (TypeError, ValueError):
            continue
        if day in days_seen:
            continue
        days_seen.add(day)
        bulk_create.append(
            RestPreference(
                rest_rule=rule,
                day_of_week=day,
                is_required=bool(item.get("is_required", False)),
            )
        )

    if bulk_create:
        RestPreference.objects.bulk_create(bulk_create)


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
        "category": position.category,
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


def _capability_payload(capability: OperatorCapability) -> dict[str, Any]:
    return {
        "id": capability.id,
        "operator_id": capability.operator_id,
        "category": capability.category,
        "skill_score": capability.skill_score,
    }


def _normalize_capability_entries(data: Any) -> Tuple[Optional[dict[str, int]], dict[str, List[str]]]:
    if data is None:
        return None, {}

    if not isinstance(data, list):
        return None, {"capabilities": ["Formato inválido para las fortalezas."]}

    normalized: dict[str, int] = {}
    errors: dict[str, List[str]] = {}

    for index, entry in enumerate(data):
        prefix = f"capabilities[{index}]"
        if not isinstance(entry, dict):
            errors.setdefault(prefix, []).append("Formato inválido.")
            continue

        category = entry.get("category")
        skill_value_raw = entry.get("skill_score")
        entry_errors = False

        if not category:
            errors.setdefault(f"{prefix}.category", []).append("Selecciona la categoría.")
            entry_errors = True
        elif category not in PositionCategory.values:
            errors.setdefault(f"{prefix}.category", []).append("Categoría inválida.")
            entry_errors = True
        elif category in normalized:
            errors.setdefault(f"{prefix}.category", []).append("La categoría ya fue registrada.")
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

        if not entry_errors and category is not None:
            normalized[category] = skill_value

    return normalized, errors


def _apply_capabilities(operator: UserProfile, capabilities: Optional[dict[str, int]]) -> None:
    if capabilities is None:
        return

    existing = {cap.category: cap for cap in operator.capabilities.all()}
    desired_categories = set(capabilities.keys())

    for category, score in capabilities.items():
        capability = existing.get(category)
        if capability:
            if capability.skill_score != score:
                capability.skill_score = score
                capability.save(update_fields=["skill_score"])
        else:
            OperatorCapability.objects.create(
                operator=operator,
                category=category,
                skill_score=score,
            )

    for category, capability in existing.items():
        if category not in desired_categories:
            capability.delete()


def _rest_rule_payload(rule: RestRule) -> dict[str, Any]:
    return {
        "id": rule.id,
        "role": {
            "id": rule.role_id,
            "label": rule.role.get_name_display(),
            "code": rule.role.name,
        },
        "shift_type": rule.shift_type,
        "min_rest_frequency": rule.min_rest_frequency,
        "min_consecutive_days": rule.min_consecutive_days,
        "max_consecutive_days": rule.max_consecutive_days,
        "post_shift_rest_days": rule.post_shift_rest_days,
        "monthly_rest_days": rule.monthly_rest_days,
        "enforce_additional_rest": rule.enforce_additional_rest,
        "active_from": rule.active_from.isoformat(),
        "active_until": rule.active_until.isoformat() if rule.active_until else None,
        "preferences": [
            {
                "id": preference.id,
                "day_of_week": preference.day_of_week,
                "label": preference.get_day_of_week_display(),
                "is_required": preference.is_required,
            }
            for preference in rule.preferred_days.all()
        ],
    }


def _overload_payload(allowance: OverloadAllowance) -> dict[str, Any]:
    return {
        "id": allowance.id,
        "role": {
            "id": allowance.role_id,
            "label": allowance.role.get_name_display(),
            "code": allowance.role.name,
        },
        "max_consecutive_extra_days": allowance.max_consecutive_extra_days,
        "highlight_level": allowance.highlight_level,
        "active_from": allowance.active_from.isoformat(),
        "active_until": allowance.active_until.isoformat() if allowance.active_until else None,
    }


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


class CalendarRulesView(LoginRequiredMixin, View):
    template_name = "calendario/rules.html"

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> Any:
        return render(request, self.template_name)


class CalendarDetailView(LoginRequiredMixin, View):
    template_name = "calendario/calendar_detail.html"

    def get(self, request: HttpRequest, pk: int, *args: Any, **kwargs: Any) -> Any:
        calendar = get_object_or_404(
            ShiftCalendar.objects.select_related("created_by", "approved_by", "base_calendar"),
            pk=pk,
        )
        date_columns, rows = _build_assignment_matrix(calendar)
        stats = _calculate_stats(rows)

        return render(
            request,
            self.template_name,
            {
                "calendar": calendar,
                "date_columns": date_columns,
                "rows": rows,
                "stats": stats,
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

                assignment.operator = operator
                assignment.alert_level = alert_level
                assignment.is_auto_assigned = False
                assignment.save(update_fields=["operator", "alert_level", "is_auto_assigned", "updated_at"])

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

                ShiftAssignment.objects.create(
                    calendar=calendar,
                    position=position,
                    date=target_date,
                    operator=operator,
                    alert_level=alert_level,
                    is_auto_assigned=False,
                )

                _refresh_workload_snapshots(calendar)
                messages.success(request, "Turno asignado manualmente.")
            else:
                messages.error(request, form.errors.as_text())
        else:
            messages.error(request, "Acción no reconocida.")

        return redirect(reverse("calendario:calendar-detail", args=[calendar.id]))


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
            PositionDefinition.objects.select_related("farm", "chicken_house")
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
            for position in PositionDefinition.objects.select_related("farm", "chicken_house")
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


class RestRuleCollectionView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> JsonResponse:
        payload, error = _load_json_body(request)
        if error:
            return error

        preferences_data = payload.pop("preferences", [])

        form = RestRuleForm(payload)
        if not form.is_valid():
            return _json_error("Datos inválidos para la regla.", errors=_form_errors(form))

        rule = form.save()
        _sync_rest_preferences(rule, preferences_data if isinstance(preferences_data, list) else [])

        rule.refresh_from_db()
        rule = RestRule.objects.select_related("role").prefetch_related("preferred_days").get(pk=rule.pk)
        return JsonResponse({"rule": _rest_rule_payload(rule)}, status=201)


class RestRuleDetailView(LoginRequiredMixin, View):
    http_method_names = ["patch", "delete"]

    def patch(self, request: HttpRequest, rule_id: int, *args: Any, **kwargs: Any) -> JsonResponse:
        rule = get_object_or_404(RestRule, pk=rule_id)
        payload, error = _load_json_body(request)
        if error:
            return error

        preferences_data = payload.pop("preferences", [])

        form = RestRuleForm(payload, instance=rule)
        if not form.is_valid():
            return _json_error("Datos inválidos para la regla.", errors=_form_errors(form))

        rule = form.save()
        _sync_rest_preferences(rule, preferences_data if isinstance(preferences_data, list) else [])

        rule.refresh_from_db()
        rule = RestRule.objects.select_related("role").prefetch_related("preferred_days").get(pk=rule.pk)
        return JsonResponse({"rule": _rest_rule_payload(rule)})

    def delete(self, request: HttpRequest, rule_id: int, *args: Any, **kwargs: Any) -> JsonResponse:
        rule = get_object_or_404(RestRule, pk=rule_id)
        rule.delete()
        return JsonResponse({"status": "deleted"})


class OverloadCollectionView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> JsonResponse:
        payload, error = _load_json_body(request)
        if error:
            return error

        form = OverloadAllowanceForm(payload)
        if not form.is_valid():
            return _json_error("Datos inválidos para la regla de sobrecarga.", errors=_form_errors(form))

        allowance = form.save()
        allowance = OverloadAllowance.objects.select_related("role").get(pk=allowance.pk)
        return JsonResponse({"overload": _overload_payload(allowance)}, status=201)


class OverloadDetailView(LoginRequiredMixin, View):
    http_method_names = ["patch", "delete"]

    def patch(self, request: HttpRequest, overload_id: int, *args: Any, **kwargs: Any) -> JsonResponse:
        allowance = get_object_or_404(OverloadAllowance, pk=overload_id)
        payload, error = _load_json_body(request)
        if error:
            return error

        form = OverloadAllowanceForm(payload, instance=allowance)
        if not form.is_valid():
            return _json_error("Datos inválidos para la regla de sobrecarga.", errors=_form_errors(form))

        allowance = form.save()
        allowance = OverloadAllowance.objects.select_related("role").get(pk=allowance.pk)
        return JsonResponse({"overload": _overload_payload(allowance)})

    def delete(self, request: HttpRequest, overload_id: int, *args: Any, **kwargs: Any) -> JsonResponse:
        allowance = get_object_or_404(OverloadAllowance, pk=overload_id)
        allowance.delete()
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

        position_qs = PositionDefinition.objects.select_related("farm", "chicken_house").prefetch_related("rooms")
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
            OperatorCapability.objects.select_related("operator")
            .prefetch_related("operator__roles")
            .order_by("operator__apellidos", "operator__nombres", "category")
        )
        capabilities_payload = [_capability_payload(capability) for capability in capability_qs]

        rest_rules_qs = (
            RestRule.objects.select_related("role")
            .prefetch_related("preferred_days")
            .order_by("role__name", "-active_from")
        )
        rest_rules_payload = [_rest_rule_payload(rule) for rule in rest_rules_qs]

        overload_qs = (
            OverloadAllowance.objects.select_related("role")
            .order_by("role__name", "-active_from")
        )
        overload_payload = [_overload_payload(allowance) for allowance in overload_qs]

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
            "rest_rules": rest_rules_payload,
            "overload_rules": overload_payload,
            "farms": farms_payload,
            "chicken_houses": chicken_houses_payload,
            "rooms": rooms_payload,
            "roles": roles_payload,
            "choice_sets": {
                "position_categories": _choice_payload(PositionCategory.choices),
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

        date_columns, rows = _build_assignment_matrix(calendar)
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

        assignment, _created = ShiftAssignment.objects.update_or_create(
            calendar=calendar,
            position=position,
            date=target_date,
            defaults={
                "operator": operator,
                "alert_level": alert_level,
                "is_auto_assigned": False,
                "is_overtime": bool(payload.get("is_overtime", False)),
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

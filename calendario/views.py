from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Dict, Iterable, List

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.http import HttpRequest, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.dateparse import parse_date
from django.views import View

from .forms import AssignmentCreateForm, AssignmentUpdateForm, CalendarGenerationForm
from .models import (
    AssignmentAlertLevel,
    CalendarStatus,
    OperatorCapability,
    PositionDefinition,
    ShiftAssignment,
    ShiftCalendar,
    complexity_score,
)
from .services import CalendarScheduler, SchedulerOptions
from users.models import UserProfile


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
    positions = list(position_query.order_by("farm__name", "code"))

    if not positions and assigned_position_ids:
        positions = list(
            PositionDefinition.objects.filter(id__in=assigned_position_ids).order_by(
                "farm__name", "code"
            )
        )
    elif not positions:
        positions = list(position_query.order_by("code"))

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
        .filter(effective_from__lte=end)
        .filter(Q(effective_until__isnull=True) | Q(effective_until__gte=start))
    )

    capability_index: dict[tuple[str, int], List[OperatorCapability]] = defaultdict(list)
    operators_by_category: dict[str, set[int]] = defaultdict(set)
    operator_cache: dict[int, Any] = {}

    for capability in capabilities_qs:
        operator = capability.operator
        operator_cache[operator.id] = operator
        capability_index[(capability.category, operator.id)].append(capability)
        operators_by_category[capability.category].add(operator.id)

    result: dict[int, dict[date, List[dict[str, Any]]]] = defaultdict(dict)

    for position in positions:
        operator_ids = operators_by_category.get(position.category, set())
        if not operator_ids:
            for day in date_columns:
                result[position.id][day] = []
            continue

        required_score = complexity_score(position.complexity)
        for day in date_columns:
            choices: List[dict[str, Any]] = []
            for operator_id in operator_ids:
                capabilities = capability_index.get((position.category, operator_id), [])
                active_caps = [cap for cap in capabilities if cap.is_active_on(day)]
                if not active_caps:
                    continue

                cap = max(active_caps, key=lambda item: complexity_score(item.max_complexity))
                max_score = complexity_score(cap.max_complexity)
                if max_score < required_score and not position.allow_lower_complexity:
                    continue

                alert = AssignmentAlertLevel.NONE
                if max_score < required_score:
                    diff = required_score - max_score
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


# ---------------------------------------------------------------------------
# Vistas HTML para usuarios no administradores
# ---------------------------------------------------------------------------


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

        calendar = ShiftCalendar.objects.create(
            name=payload.get("name", ""),
            start_date=start_date,
            end_date=end_date,
            status=CalendarStatus.DRAFT,
            created_by=request.user,
            notes=payload.get("notes", ""),
        )

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

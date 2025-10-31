import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable, Mapping, Optional, Sequence

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q, QuerySet
from django.http import JsonResponse, QueryDict
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.text import slugify
from django.utils.translation import gettext as _
from django.views import View, generic

from .forms import TaskDefinitionQuickCreateForm
from .models import TaskCategory, TaskDefinition, TaskStatus
from personal.models import DayOfWeek, PositionDefinition, UserProfile
from production.models import ChickenHouse, Farm, Room


class TaskManagerHomeView(generic.TemplateView):
    """Render a placeholder landing page for the task manager module."""

    template_name = "task_manager/index.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault("task_definition_form", TaskDefinitionQuickCreateForm())
        categories = TaskCategory.objects.filter(is_active=True).order_by("name")
        statuses = TaskStatus.objects.filter(is_active=True).order_by("name")
        context["task_manager_categories"] = categories
        context["task_manager_statuses"] = statuses

        filters = build_task_definition_filters(self.request.GET)
        defaults = TaskDefinitionFilters()

        status_groups = build_status_filter_groups(statuses)
        status_value, status_label = ensure_filter_selection(filters, "status", status_groups, defaults.status)
        category_groups = build_category_filter_groups(categories)
        category_value, category_label = ensure_filter_selection(
            filters, "category", category_groups, defaults.category
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
        paginator = Paginator(queryset, 20)
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
        raw_group_primary = (self.request.GET.get("group_primary") or "none").strip() or "none"
        group_primary_value = (
            raw_group_primary
            if resolve_option_label(group_primary_groups, raw_group_primary) is not None
            else "none"
        )
        group_primary_label = (
            resolve_option_label(group_primary_groups, group_primary_value) or _("Sin agrupación")
        )

        group_secondary_groups = build_grouping_secondary_filter_groups()
        raw_group_secondary = (self.request.GET.get("group_secondary") or "none").strip() or "none"
        group_secondary_value = (
            raw_group_secondary
            if resolve_option_label(group_secondary_groups, raw_group_secondary) is not None
            else "none"
        )
        if group_secondary_value == group_primary_value:
            group_secondary_value = "none"
        group_secondary_label = (
            resolve_option_label(group_secondary_groups, group_secondary_value) or _("No aplicar")
        )

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
        return context


task_manager_home_view = TaskManagerHomeView.as_view()


def _build_telegram_mini_app_payload(*, date_label: str, display_name: str, username: str, role: str, initials: str) -> dict[str, object]:
    tasks = [
        {
            "title": "Checklist apertura galpon",
            "tone": "brand",
            "badges": [
                {"label": "Recurrente", "theme": "brand"},
                {"label": "Prioridad media", "theme": "neutral"},
            ],
            "description": "Completar antes de iniciar el turno. Verifica ventilacion, bebederos y bioseguridad.",
            "meta": [
                "Turno: Diurno - Posicion auxiliar operativo",
                "Asignada por: Supervisor bioseguridad",
            ],
            "actions": [
                {"label": "Marcar completada", "action": "complete"},
                {"label": "Agregar evidencia", "action": "evidence"},
                {"label": "Postergar", "action": "snooze"},
            ],
        },
        {
            "title": "Reporte correctivo galpon 2",
            "tone": "critical",
            "badges": [{"label": "Unica - Vencida", "theme": "critical"}],
            "description": "Registrar hallazgos del recorrido nocturno y adjuntar fotografias.",
            "meta": [
                "Turno: Nocturno - Posicion lider de turno",
                "Asignada para: 03 Nov - Vence hoy",
            ],
            "actions": [
                {"label": "Enviar evidencia", "action": "evidence"},
                {"label": "Agregar nota", "action": "note"},
                {"label": "Solicitar ayuda", "action": "assist"},
            ],
        },
        {
            "title": "Capacitacion protocolos",
            "tone": "success",
            "badges": [{"label": "Extra voluntaria", "theme": "success"}],
            "description": "Participa en la sesion de actualizacion de protocolos de higiene. Suma puntos adicionales.",
            "meta": [
                "Horario: 16:00 - Sala formacion",
                "Reportada por: Gabriela Melo",
            ],
            "actions": [
                {"label": "Acepto realizarla", "action": "accept"},
                {"label": "Dejar en pull", "action": "pull"},
            ],
        },
        {
            "title": "Descanso programado",
            "tone": "neutral",
            "badges": [{"label": "Automatico", "theme": "neutral"}],
            "description": "Descanso compensatorio despues de 6 dias de racha. No se asignan tareas en esta franja.",
            "meta": [
                "Fecha: 05 Nov - Proximo turno nocturno",
                "Generado automaticamente para balancear jornada",
            ],
            "actions": [
                {"label": "Ver historial", "action": "history"},
                {"label": "Solicitar cambio", "action": "request"},
            ],
        },
    ]
    return {
        "date_label": date_label,
        "user": {
            "display_name": display_name,
            "username": username,
            "role": role,
            "avatar_initials": initials,
        },
        "tasks": tasks,
        "current_shift": {
            "label": "Turno nocturno - Galpon 3",
            "position": "Posicion: Auxiliar operativo",
            "next": "Proximo turno: 05 Nov - 22:00",
        },
        "scorecard": {
            "points": 122,
            "streak": "Racha vigente: 6 dias cumplidos",
            "extras": "Tareas extra reportadas: 3",
            "message": "Sigue reportando iniciativas. Cada aporte aprobado suma 15 puntos.",
        },
        "suggestions": [
            "Mantenimiento preventivo ventiladores - pendiente revision del staff.",
            "Mejora en checklist de bioseguridad - aprobado y publicado.",
        ],
        "history": [
            {"label": "03 Nov", "summary": "3 tareas completadas - 1 postergada"},
            {"label": "02 Nov", "summary": "4 tareas completadas"},
            {"label": "01 Nov", "summary": "Descanso programado"},
        ],
    }


class TaskManagerTelegramMiniAppView(generic.TemplateView):
    """Render the operator experience for the Telegram mini app with integration hooks."""

    template_name = "task_manager/telegram_mini_app.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.localdate()
        user = getattr(self.request, "user", None)
        if getattr(user, "is_authenticated", False):
            raw_full_name = user.get_full_name() or ""
            raw_username = user.get_username() or ""
        else:
            raw_full_name = ""
            raw_username = ""

        display_name = (raw_full_name or raw_username or "Operario invitado").strip()
        username = raw_username.strip()
        initials = "".join(part[0] for part in display_name.split() if part).upper()[:2] or "OP"

        context["telegram_mini_app"] = _build_telegram_mini_app_payload(
            date_label=date_format(today, "DATE_FORMAT"),
            display_name=display_name,
            username=username,
            role="Operario",
            initials=initials,
        )
        context["telegram_integration_enabled"] = True
        return context


class TaskManagerTelegramMiniAppDemoView(generic.TemplateView):
    """Render a simplified, unauthenticated preview of the Telegram mini app."""

    template_name = "task_manager/telegram_mini_app.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.localdate()
        display_name = "Operario demo"
        initials = "".join(part[0] for part in display_name.split() if part).upper()[:2] or "OP"
        context["telegram_mini_app"] = _build_telegram_mini_app_payload(
            date_label=date_format(today, "DATE_FORMAT"),
            display_name=display_name,
            username="",
            role="Vista previa",
            initials=initials,
        )
        context["telegram_integration_enabled"] = False
        return context


telegram_mini_app_view = TaskManagerTelegramMiniAppView.as_view()
telegram_mini_app_demo_view = TaskManagerTelegramMiniAppDemoView.as_view()


def _task_definition_redirect_url(task_id: int) -> str:
    base_url = reverse("task_manager:index")
    return f"{base_url}?tm_task={task_id}#tm-tareas"


def serialize_task_definition(task: TaskDefinition) -> dict[str, object]:
    return {
        "id": task.pk,
        "name": task.name,
        "description": task.description,
        "status": task.status_id,
        "category": task.category_id,
        "task_type": task.task_type,
        "scheduled_for": task.scheduled_for.isoformat() if task.scheduled_for else None,
        "weekly_days": list(task.weekly_days or []),
        "month_days": list(task.month_days or []),
        "position": task.position_id,
        "collaborator": task.collaborator_id,
        "farms": list(task.farms.values_list("pk", flat=True)),
        "chicken_houses": list(task.chicken_houses.values_list("pk", flat=True)),
        "rooms": list(task.rooms.values_list("pk", flat=True)),
        "evidence_requirement": task.evidence_requirement,
        "record_format": task.record_format,
    }


class TaskDefinitionCreateView(LoginRequiredMixin, View):
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


class TaskDefinitionDetailView(LoginRequiredMixin, View):
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


class TaskDefinitionUpdateView(LoginRequiredMixin, View):
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


class TaskDefinitionDeleteView(LoginRequiredMixin, View):
    """Delete an existing task definition after confirmation."""

    http_method_names = ["post"]

    def post(self, request, pk: int, *args, **kwargs):
        task_definition = get_object_or_404(TaskDefinition, pk=pk)
        task_name = task_definition.name
        task_definition.delete()
        messages.success(
            request,
            _('La tarea "%(name)s" se eliminó correctamente.') % {"name": task_name},
        )
        redirect_url = f"{reverse('task_manager:index')}#tm-tareas"
        return redirect(redirect_url)


task_definition_delete_view = TaskDefinitionDeleteView.as_view()


class TaskDefinitionListView(LoginRequiredMixin, View):
    """Return rendered task rows for incremental loading."""

    http_method_names = ["get"]

    def get(self, request, *args, **kwargs):
        if request.headers.get("X-Requested-With") != "XMLHttpRequest":
            return JsonResponse({"detail": _("Solicitud inválida.")}, status=400)

        filters = build_task_definition_filters(request.GET)
        queryset = get_task_definition_queryset(filters)
        paginator = Paginator(queryset, 20)
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


class TaskDefinitionReorderView(LoginRequiredMixin, View):
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
    name: str
    description: str
    category_label: str
    status_label: str
    status_is_active: bool
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


@dataclass(frozen=True)
class TaskDefinitionGroupValue:
    key: str
    label: str
    subtitle: Optional[str] = None


TASK_FILTER_PARAM_NAMES: tuple[str, ...] = (
    "status",
    "category",
    "scope",
    "responsible",
    "scheduled_start",
    "scheduled_end",
)


@dataclass
class TaskDefinitionFilters:
    status: str = "all"
    category: str = "all"
    scope: str = "all"
    responsible: str = "all"
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
            "farms",
            "chicken_houses__farm",
            "rooms__chicken_house__farm",
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
        queryset = queryset.filter(status_id=status_id)

    category_value = filters.category
    category_id = _parse_positive_int(category_value)
    if category_id is not None:
        queryset = queryset.filter(category_id=category_id)

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
                farms__isnull=True,
                chicken_houses__isnull=True,
                rooms__isnull=True,
                position__farm__isnull=True,
                position__chicken_house__isnull=True,
            )
        elif prefix == "farm":
            if scope_id is None:
                queryset = queryset.filter(
                    Q(farms__isnull=False)
                    | Q(chicken_houses__farm__isnull=False)
                    | Q(rooms__chicken_house__farm__isnull=False)
                    | Q(position__farm__isnull=False)
                )
            else:
                queryset = queryset.filter(
                    Q(farms__pk=scope_id)
                    | Q(chicken_houses__farm__pk=scope_id)
                    | Q(rooms__chicken_house__farm__pk=scope_id)
                    | Q(position__farm_id=scope_id)
                )
        elif prefix == "house":
            if scope_id is None:
                queryset = queryset.filter(
                    Q(chicken_houses__isnull=False)
                    | Q(rooms__chicken_house__isnull=False)
                    | Q(position__chicken_house__isnull=False)
                )
            else:
                queryset = queryset.filter(
                    Q(chicken_houses__pk=scope_id)
                    | Q(rooms__chicken_house__pk=scope_id)
                    | Q(position__chicken_house_id=scope_id)
                )
        elif prefix == "room":
            if scope_id is None:
                queryset = queryset.filter(rooms__isnull=False)
            else:
                queryset = queryset.filter(rooms__pk=scope_id)
        else:
            needs_distinct = False

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

        status_label = task.status.name if getattr(task, "status", None) and task.status.name else _("Sin estado")
        status_key_value = (
            f"status:{task.status_id}"
            if task.status_id
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
                name=task.name,
                description=task.description,
                category_label=task.category.name,
                status_label=task.status.name,
                status_is_active=task.status.is_active,
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
        PositionDefinition.objects.select_related("farm", "chicken_house")
        .order_by("display_order", "name")
        .only("id", "name", "farm__name", "chicken_house__name")
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


def build_grouping_primary_filter_groups() -> Sequence[FilterOptionGroup]:
    options = [
        FilterOption("none", _("Sin agrupación")),
        FilterOption("status", _("Estado")),
        FilterOption("category", _("Categoría")),
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
    rooms = list(task.rooms.all())
    if rooms:
        return summarize_related_scope(
            (f"{room.chicken_house.farm.name} · {room.chicken_house.name} · {room.name}" for room in rooms),
            _("Salón"),
        ), "room", _("Salones")

    chicken_houses = list(task.chicken_houses.all())
    if chicken_houses:
        return summarize_related_scope(
            (f"{house.farm.name} · {house.name}" for house in chicken_houses),
            _("Galpón"),
        ), "house", _("Galpones")

    farms = list(task.farms.all())
    if farms:
        return summarize_related_scope((farm.name for farm in farms), _("Granja")), "farm", _("Granjas")

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

from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db.models import Q, QuerySet
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.formats import date_format
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
        highlight_raw = self.request.GET.get("tm_task")
        highlight_id: Optional[int] = None
        if highlight_raw is not None:
            try:
                highlight_id = int(highlight_raw)
            except (TypeError, ValueError):
                highlight_id = None

        queryset = get_task_definition_queryset()
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
        if "page" in remaining_params:
            remaining_params.pop("page")
        querystring = remaining_params.urlencode()
        context["task_definition_querystring"] = querystring
        if querystring:
            query_prefix = f"?{querystring}&"
        else:
            query_prefix = "?"
        context["task_definition_page_query_prefix"] = query_prefix

        if paginator.count:
            start_index = (page_obj.number - 1) * paginator.per_page + 1
            end_index = start_index + len(page_obj.object_list) - 1
        else:
            start_index = 0
            end_index = 0
        context["task_definition_page_start"] = start_index
        context["task_definition_page_end"] = end_index
        context["task_manager_status_filter"] = FilterPickerData(
            default_value="all",
            default_label=_("Todos los estados"),
            groups=build_status_filter_groups(statuses),
        )
        context["task_manager_category_filter"] = FilterPickerData(
            default_value="all",
            default_label=_("Todas las categorías"),
            groups=build_category_filter_groups(categories),
        )
        context["task_manager_scope_filter"] = FilterPickerData(
            default_value="all",
            default_label=_("Todos los lugares"),
            groups=build_scope_filter_groups(),
            search_enabled=True,
        )
        context["task_manager_task_type_filter"] = FilterPickerData(
            default_value="all",
            default_label=_("Cualquier tipo"),
            groups=build_task_type_filter_groups(),
        )
        context["task_manager_responsible_filter"] = FilterPickerData(
            default_value="all",
            default_label=_("Todos los responsables"),
            groups=build_responsible_filter_groups(),
            search_enabled=True,
        )
        context["task_manager_created_window_filter"] = FilterPickerData(
            default_value="all",
            default_label=_("Cualquier fecha"),
            groups=build_created_window_filter_groups(),
        )
        context["task_manager_group_primary_filter"] = FilterPickerData(
            default_value="none",
            default_label=_("Sin agrupación"),
            groups=build_grouping_primary_filter_groups(),
        )
        context["task_manager_group_secondary_filter"] = FilterPickerData(
            default_value="none",
            default_label=_("No aplicar"),
            groups=build_grouping_secondary_filter_groups(),
        )
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

        queryset = get_task_definition_queryset()
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


def get_task_definition_queryset() -> QuerySet[TaskDefinition]:
    return (
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
        .order_by("name", "pk")
    )


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
        FilterOption("all", _("Todos los responsables")),
        FilterOption("collaborator", _("Con colaborador asignado")),
        FilterOption("position", _("Con posición sugerida")),
        FilterOption("unassigned", _("Sin responsable definido")),
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


def build_task_type_filter_groups() -> Sequence[FilterOptionGroup]:
    groups = [
        FilterOptionGroup(
            key="generic",
            label=_("General"),
            options=[FilterOption("all", _("Cualquier tipo"))],
        )
    ]
    type_options = [
        FilterOption(TaskDefinition.TaskType.RECURRING, TaskDefinition.TaskType.RECURRING.label),
        FilterOption(TaskDefinition.TaskType.ONE_TIME, TaskDefinition.TaskType.ONE_TIME.label),
    ]
    groups.append(
        FilterOptionGroup(
            key="types",
            label=_("Tipos disponibles"),
            options=type_options,
        )
    )
    return groups


def build_created_window_filter_groups() -> Sequence[FilterOptionGroup]:
    window_options = [
        FilterOption("all", _("Cualquier fecha")),
        FilterOption("today", _("Hoy")),
        FilterOption("yesterday", _("Ayer")),
        FilterOption("last_3_days", _("Últimos 3 días")),
        FilterOption("last_7_days", _("Últimos 7 días")),
        FilterOption("this_week", _("Esta semana")),
        FilterOption("next_3_days", _("Próximos 3 días")),
        FilterOption("next_7_days", _("Próximos 7 días")),
        FilterOption("next_14_days", _("Próximas 2 semanas")),
    ]
    return [
        FilterOptionGroup(
            key="created-window",
            label=_("Rangos de tiempo"),
            options=window_options,
        )
    ]


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
        Q(name__lt=task.name) | (Q(name=task.name) & Q(pk__lt=task.pk))
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

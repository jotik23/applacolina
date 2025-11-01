import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Iterable, Mapping, Optional, Sequence, Tuple
from urllib.parse import parse_qsl

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
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

from notifications.models import TelegramBotConfig, TelegramChatLink

from .forms import MiniAppAuthenticationForm, TaskDefinitionQuickCreateForm
from .models import TaskCategory, TaskDefinition, TaskStatus
from personal.models import DayOfWeek, PositionDefinition, UserProfile
from production.models import ChickenHouse, Farm, Room


class MiniAppClient(Enum):
    TELEGRAM = "telegram"
    EMBEDDED = "embedded"
    WEB = "web"


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


def _resolve_default_mini_app_bot() -> Optional[TelegramBotConfig]:
    """Return the Telegram bot configured for the mini app, if any."""

    configured_name = getattr(settings, "TASK_MANAGER_MINI_APP_BOT_NAME", "").strip()
    queryset = TelegramBotConfig.objects.filter(is_active=True)

    if configured_name:
        bot = queryset.filter(name=configured_name).first()
        if bot:
            return bot

    return queryset.order_by("name").first()


def _default_auth_backend() -> str:
    """Return the default authentication backend for explicit login calls."""

    backends = getattr(settings, "AUTHENTICATION_BACKENDS", None)
    if backends:
        if isinstance(backends, (list, tuple)) and backends:
            return backends[0]
        if isinstance(backends, str):
            return backends

    return "django.contrib.auth.backends.ModelBackend"


def _get_telegram_init_data(request) -> Optional[str]:
    """Extract the signed init data sent by Telegram Web Apps."""

    candidates = [
        request.GET.get("tgWebAppData"),
        request.GET.get("init_data"),
        request.GET.get("telegram_init_data"),
        request.META.get("HTTP_X_TELEGRAM_INIT_DATA"),
    ]

    for candidate in candidates:
        if candidate:
            return candidate
    return None


def _verify_telegram_signature(init_data: str, *, bot_token: str) -> dict[str, str]:
    """Validate the Telegram init data signature and return the parsed payload."""

    parsed_pairs = dict(parse_qsl(init_data, strict_parsing=True))
    received_hash = parsed_pairs.pop("hash", None)
    if not received_hash:
        raise ValueError("Telegram no incluyó la firma de verificación.")

    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(parsed_pairs.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(calculated_hash, received_hash):
        raise ValueError("La firma de Telegram no es válida.")

    return parsed_pairs


def _extract_telegram_user(payload: Mapping[str, str]) -> dict[str, object]:
    """Load the Telegram user information from the init data payload."""

    user_json = payload.get("user")
    if not user_json:
        raise ValueError("Telegram no incluyó la información del usuario.")

    try:
        user_payload = json.loads(user_json)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive parsing
        raise ValueError("No se pudo interpretar la información del usuario de Telegram.") from exc

    if "id" not in user_payload:
        raise ValueError("El payload de Telegram no incluye el identificador del usuario.")

    return user_payload


def _resolve_primary_group_label(user: UserProfile) -> Optional[str]:
    """Return the name of the first group associated to the user, if any."""

    if not hasattr(user, "groups"):
        return None

    group = user.groups.order_by("name").first()
    return group.name if group else None


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


def _build_telegram_mini_app_payload(
    *,
    date_label: str,
    display_name: str,
    contact_handle: str,
    role: str,
    initials: str,
) -> dict[str, object]:
    today = timezone.localdate()
    weekday_label = date_format(today, "l").capitalize()
    day_number = date_format(today, "d")
    month_label = date_format(today, "M").strip(".").lower()
    cartons_per_pack = 30
    daily_cartons = 820
    daily_eggs = daily_cartons * cartons_per_pack

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
        "active_hens": 26800,
        "label": _("Aves en postura activas"),
        "target_posture_percent": 92.0,
    }

    egg_workflow = {
        "cartons_per_pack": cartons_per_pack,
        "batch": {
            "label": "Lote GS-2024-11",
            "origin": "Granja San Lucas · Galpón 3",
            "rooms": ["Sala 1", "Sala 2"],
            "produced_cartons": daily_cartons,
            "produced_eggs": daily_eggs,
            "recorded_at": date_format(today, "d M Y"),
        },
        "stages": [
            {
                "id": "transport",
                "icon": "🚚",
                "title": "Transporte interno",
                "tone": "brand",
                "status": "pending",
                "summary": "Traslada la producción registrada hacia el centro de acopio sin perder trazabilidad.",
                "metrics": [
                    {"label": "Cartones cargados", "value": daily_cartons, "unit": "cartones"},
                    {"label": "Unidades", "value": daily_eggs, "unit": "huevos"},
                ],
                "route": {
                    "origin": "Galpón 3 · Salas 1 y 2",
                    "destination": "Centro de clasificación & inspección",
                },
                "progress_steps": [
                    {"id": "verified", "label": "Verificado"},
                    {"id": "loaded", "label": "Cargado"},
                    {"id": "departed", "label": "Iniciar transporte"},
                    {"id": "arrival", "label": "En destino"},
                    {"id": "unloading", "label": "Descargando"},
                    {"id": "completed", "label": "Completado"},
                ],
                "checkpoints": [
                    "Confirma estado de guacales y temperatura.",
                    "Toma foto rápida del cargue si hay novedades.",
                ],
            },
            {
                "id": "verification",
                "icon": "📦",
                "title": "Verificación en acopio",
                "tone": "sky",
                "status": "pending",
                "summary": "Valida que lo recibido coincide con lo transportado y reporta ajustes en línea.",
                "metrics": [
                    {"label": "Cartones esperados", "value": daily_cartons, "unit": "cartones"},
                    {"label": "Huevos esperados", "value": daily_eggs, "unit": "huevos"},
                ],
                "fields": [
                    {"id": "cartons_received", "label": "Cartones recibidos", "placeholder": str(daily_cartons)},
                    {"id": "eggs_damaged", "label": "Huevos fisurados", "placeholder": "0"},
                    {"id": "temperature", "label": "Temperatura (°C)", "placeholder": "25"},
                ],
                "checkpoints": [
                    "Anota diferencias en cartones o unidades.",
                    "Escanea QR de trazabilidad antes de firmar.",
                ],
            },
            {
                "id": "classification",
                "icon": "🥚",
                "title": "Clasificación por calibres",
                "tone": "emerald",
                "status": "pending",
                "summary": "Distribuye los huevos por calibre y conserva la equivalencia con el lote recibido.",
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
                "summary": "Registra hallazgos sanitarios y libera el lote para despacho.",
                "metrics": [
                    {"label": "Lotes revisados", "value": 1, "unit": "lote"},
                    {"label": "Cartones listos", "value": daily_cartons - 5, "unit": "cartones"},
                    {"label": "Cartones retenidos", "value": 5, "unit": "cartones"},
                ],
                "fields": [
                    {
                        "id": "notes",
                        "label": "Observaciones",
                        "placeholder": "Ej: Retener 5 cartones para revisión",
                        "multiline": True,
                    },
                    {"id": "released_by", "label": "Inspector", "placeholder": "Nombre del responsable"},
                    {"id": "release_time", "label": "Hora de liberación", "placeholder": "hh:mm"},
                ],
                "checkpoints": [
                    "Confirma limpieza de área y temperatura de cámara.",
                    "Marca cartones retenidos y notifica al supervisor.",
                ],
            },
        ],
    }

    tomorrow = today + timedelta(days=1)
    day_minus_1 = today - timedelta(days=1)
    day_minus_2 = today - timedelta(days=2)
    day_minus_3 = today - timedelta(days=3)

    transport_lot_backlog = [
        {
            "id": "GS-2024-11",
            "label": "Lote GS-2024-11",
            "farm": "Granja San Lucas",
            "barn": "Galpón 3",
            "rooms": ["Sala 1", "Sala 2"],
            "cartons": 420,
            "production_date_iso": day_minus_1.isoformat(),
            "production_date_label": date_format(day_minus_1, "DATE_FORMAT"),
            "status": _("Prioridad alta"),
        },
        {
            "id": "PR-2024-08",
            "label": "Lote PR-2024-08",
            "farm": "Granja Providencia",
            "barn": "Galpón 5",
            "rooms": ["Sala 2"],
            "cartons": 310,
            "production_date_iso": day_minus_2.isoformat(),
            "production_date_label": date_format(day_minus_2, "DATE_FORMAT"),
            "status": _("Listo para cargar"),
        },
        {
            "id": "LP-2024-03",
            "label": "Lote LP-2024-03",
            "farm": "Granja La Primavera",
            "barn": "Galpón 1",
            "rooms": [],
            "cartons": 280,
            "production_date_iso": day_minus_3.isoformat(),
            "production_date_label": date_format(day_minus_3, "DATE_FORMAT"),
            "status": _("Alerta: revisar humedad"),
        },
    ]

    transport_total_cartons = sum(lot["cartons"] for lot in transport_lot_backlog)

    transport_queue = {
        "title": _("Lotes listos para transporte"),
        "pending_count": len(transport_lot_backlog),
        "total_cartons": transport_total_cartons,
        "lots": transport_lot_backlog,
        "transporters": [
            {"id": "transcolina", "label": "Transcolina logística (4 camiones)", "contact": "+57 316 555 0101"},
            {"id": "coopverde", "label": "Cooperativa Ruta Verde - Línea 2", "contact": "+57 310 889 4477"},
            {"id": "flota-propia", "label": "Flota interna La Colina", "contact": "+57 300 111 2233"},
        ],
        "default_transporter_id": "transcolina",
        "default_expected_date_iso": tomorrow.isoformat(),
        "default_expected_date_label": date_format(tomorrow, "DATE_FORMAT"),
        "instructions": _(
            "Selecciona los lotes y asigna el transportador para autorizar el traslado interno."
        ),
    }

    shift_confirmation = {
        "date_label": _("Hoy, %(weekday)s %(day)s de %(month)s")
        % {"weekday": weekday_label, "day": day_number, "month": month_label},
        "category_label": "Operaciones - Bioseguridad",
        "position_label": "Auxiliar operativo",
        "farm": "Granja La Colina",
        "barn": "Galpón 3",
        "rooms": ["Sala 1", "Sala 2"],
        "handoff_from": "Camilo Ortiz",
        "handoff_to": "Lucía Pérez",
        "requires_confirmation": True,
        "confirmed": False,
        "storage_key": f"miniapp-shift-confirm::{today.isoformat()}",
    }

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
            "reward_points": 25,
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
            "reward_points": 32,
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
            "reward_points": 18,
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
            "reward_points": 10,
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
        "egg_workflow": egg_workflow,
        "transport_queue": transport_queue,
        "tasks": tasks,
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
        "shift_confirmation": shift_confirmation,
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
        self.telegram_auth_error: Optional[str] = None
        self.telegram_user_payload: Optional[dict[str, object]] = None

        if self.mini_app_client == MiniAppClient.TELEGRAM and not request.user.is_authenticated:
            user, error = self._attempt_telegram_login(request)
            if user:
                backend = _default_auth_backend()
                user.backend = backend  # type: ignore[attr-defined]
                login(request, user)
            else:
                self.telegram_auth_error = error

        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        form = self.form_class(request=request, data=request.POST)
        if form.is_valid():
            login(request, form.get_user())
            return redirect(request.path)

        return self.render_to_response(self.get_context_data(form=form))

    def _attempt_telegram_login(self, request) -> Tuple[Optional[UserProfile], Optional[str]]:
        init_data = _get_telegram_init_data(request)
        if not init_data:
            return None, _("No se recibieron las credenciales de Telegram.")

        bot = _resolve_default_mini_app_bot()
        if not bot:
            return None, _("No hay un bot de Telegram activo configurado para la mini app.")

        try:
            payload = _verify_telegram_signature(init_data, bot_token=bot.token)
            user_payload = _extract_telegram_user(payload)
        except ValueError as exc:
            return None, str(exc)

        telegram_user_id = user_payload.get("id")
        chat_link = (
            TelegramChatLink.objects.filter(bot=bot, telegram_user_id=telegram_user_id)
            .select_related("user")
            .first()
        )

        if not chat_link:
            return None, _("No encontramos un chat verificado asociado a tu cuenta de Telegram.")

        if chat_link.status != TelegramChatLink.Status.VERIFIED:
            return None, _("Tu chat de Telegram aún no ha sido verificado.")

        user = chat_link.user
        if not getattr(user, "is_active", False):
            return None, _("Tu cuenta está inactiva. Contacta al administrador.")

        if not user.has_perm("task_manager.access_mini_app"):
            return None, _("No tienes permisos para acceder a la mini app.")

        self.telegram_user_payload = user_payload
        self._refresh_chat_link_metadata(chat_link, user_payload)
        return user, None

    def _refresh_chat_link_metadata(self, chat_link: TelegramChatLink, user_payload: Mapping[str, object]) -> None:
        update_fields: list[str] = []
        for attr in ("username", "first_name", "last_name", "language_code"):
            value = user_payload.get(attr)
            if value is None:
                continue
            if getattr(chat_link, attr) != value:
                setattr(chat_link, attr, value)
                update_fields.append(attr)

        chat_link.last_interaction_at = timezone.now()
        update_fields.append("last_interaction_at")

        if update_fields:
            if "updated_at" not in update_fields:
                update_fields.append("updated_at")
            chat_link.save(update_fields=update_fields)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.localdate()
        user = getattr(self.request, "user", None)

        has_authenticated_user = getattr(user, "is_authenticated", False)
        has_access = has_authenticated_user and user.has_perm("task_manager.access_mini_app")

        if has_access:
            display_name = (user.get_full_name() or user.get_username() or "Operario").strip()
            phone_number = getattr(user, "telefono", "") or ""
            phone_number = str(phone_number).strip()
            contact_handle = f"@{phone_number}" if phone_number else "@Sin teléfono"
            role_label = _resolve_primary_group_label(user) or "Operario"
            initials = "".join(part[0] for part in display_name.split() if part).upper()[:2] or "OP"
            context["telegram_mini_app"] = _build_telegram_mini_app_payload(
                date_label=date_format(today, "DATE_FORMAT"),
                display_name=display_name,
                contact_handle=contact_handle,
                role=role_label,
                initials=initials,
            )
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
        context["telegram_auth_error"] = self.telegram_auth_error
        context["mini_app_access_granted"] = has_access

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
            contact_handle="@demo",
            role="Vista previa",
            initials=initials,
        )
        context["telegram_integration_enabled"] = False
        return context


def mini_app_logout_view(request):
    if request.method == "POST":
        logout(request)
        return redirect("task_manager:telegram-mini-app")

    return redirect("task_manager:telegram-mini-app")


telegram_mini_app_view = TaskManagerMiniAppView.as_view()
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
        "is_mandatory": task.is_mandatory,
        "criticality_level": task.criticality_level,
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

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple
from urllib.parse import parse_qsl

from django.conf import settings
from django.contrib import messages
from django.contrib.humanize.templatetags.humanize import intcomma
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
    tomorrow = today + timedelta(days=1)
    day_minus_1 = today - timedelta(days=1)
    day_minus_2 = today - timedelta(days=2)
    day_minus_3 = today - timedelta(days=3)
    day_plus_2 = today + timedelta(days=2)

    transport_manifest_entries = [
        {
            "id": "GS-2024-11-G3",
            "label": "Granja San Lucas · Galpón 3",
            "farm": "Granja San Lucas",
            "barn": "Galpón 3",
            "rooms": ["Sala 1", "Sala 2"],
            "cartons": 240,
            "production_date_iso": day_minus_1.isoformat(),
            "production_date_label": date_format(day_minus_1, "DATE_FORMAT"),
            "tag": _("Día reciente"),
        },
        {
            "id": "GS-2024-11-G4",
            "label": "Granja San Lucas · Galpón 4",
            "farm": "Granja San Lucas",
            "barn": "Galpón 4",
            "rooms": ["Sala 1"],
            "cartons": 200,
            "production_date_iso": day_minus_1.isoformat(),
            "production_date_label": date_format(day_minus_1, "DATE_FORMAT"),
            "tag": _("Compartido"),
        },
        {
            "id": "PR-2024-08-G5",
            "label": "Granja Providencia · Galpón 5",
            "farm": "Granja Providencia",
            "barn": "Galpón 5",
            "rooms": ["Sala 2"],
            "cartons": 195,
            "production_date_iso": day_minus_2.isoformat(),
            "production_date_label": date_format(day_minus_2, "DATE_FORMAT"),
            "tag": _("Día anterior"),
        },
        {
            "id": "LP-2024-03-G1",
            "label": "Granja La Primavera · Galpón 1",
            "farm": "Granja La Primavera",
            "barn": "Galpón 1",
            "rooms": [],
            "cartons": 185,
            "production_date_iso": day_minus_3.isoformat(),
            "production_date_label": date_format(day_minus_3, "DATE_FORMAT"),
            "tag": _("Revisión"),
        },
    ]
    transport_manifest_total_cartons = sum(entry["cartons"] for entry in transport_manifest_entries)
    transport_manifest_count = len(transport_manifest_entries)
    transport_manifest_origin = _("%(count)s lotes · múltiples granjas") % {"count": transport_manifest_count}
    verification_reference_entry = transport_manifest_entries[0] if transport_manifest_entries else None
    verification_lot = None
    if verification_reference_entry:
        verification_lot = {
            "id": verification_reference_entry["id"],
            "label": verification_reference_entry["label"],
            "farm": verification_reference_entry["farm"],
            "barn": verification_reference_entry["barn"],
            "production_date_label": verification_reference_entry["production_date_label"],
            "cartons": verification_reference_entry["cartons"],
            "rooms": verification_reference_entry.get("rooms", []),
        }

    cartons_per_pack = 30
    daily_cartons = transport_manifest_total_cartons
    daily_eggs = daily_cartons * cartons_per_pack
    inspection_reference_entry = transport_manifest_entries[0]
    inspection_production_date_label = inspection_reference_entry["production_date_label"]
    inspection_farm = inspection_reference_entry["farm"]
    inspection_barn = inspection_reference_entry["barn"]
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
        "active_hens": 26800,
        "label": _("Aves en postura activas"),
        "target_posture_percent": 92.0,
    }

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
        "stages": [
            {
                "id": "transport",
                "icon": "🚚",
                "title": "Transporte interno",
                "tone": "brand",
                "status": "pending",
                "summary": _(
                    "Consolida las cargas de distintos galpones y fechas en un solo despacho hacia el centro de acopio."
                ),
                "metrics": [
                    {"label": _("Cartones en manifiesto"), "value": daily_cartons, "unit": "cartones"},
                ],
                "route": {
                    "origin": transport_manifest_origin,
                    "destination": _("Centro de clasificación & inspección"),
                },
                "manifest": {
                    "entries": transport_manifest_entries,
                    "total_cartons": daily_cartons,
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
                "lot": verification_lot,
                "fields": [
                    {
                        "id": "cartons_received",
                        "label": "Cartones recibidos",
                        "placeholder": str(daily_cartons),
                        "input_type": "number",
                    },
                    {
                        "id": "eggs_damaged",
                        "label": "Huevos fisurados",
                        "placeholder": "0",
                        "input_type": "number",
                    },
                ],
                "checkpoints": [
                    "Anota diferencias en cartones o unidades.",
                    "Escanea QR de trazabilidad antes de firmar.",
                ],
            },
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
        "pending_classification": pending_classification_summary,
        "egg_workflow": egg_workflow,
        "transport_queue": transport_queue,
        "tasks": tasks,
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

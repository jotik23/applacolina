from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Sequence, Tuple

from django.db.models import Count, Prefetch, Sum, Q
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _

from administration.models import PurchaseApproval, PurchaseRequest, PurchasingExpenseType, Supplier
from administration.models import Product as AdministrationProduct, PurchaseItem
from production.models import ChickenHouse, Farm
from personal.models import UserProfile


MAX_PURCHASE_ENTRIES = 6
MAX_APPROVAL_ENTRIES = 6
MAX_PURCHASE_REQUEST_ITEMS = 12
RECENT_SUPPLIER_SUGGESTIONS = 12

PURCHASE_STATUS_THEME: dict[str, str] = {
    PurchaseRequest.Status.DRAFT: "slate",
    PurchaseRequest.Status.SUBMITTED: "amber",
    PurchaseRequest.Status.APPROVED: "brand",
    PurchaseRequest.Status.RECEPTION: "orange",
    PurchaseRequest.Status.INVOICE: "emerald",
    PurchaseRequest.Status.PAYMENT: "emerald",
    PurchaseRequest.Status.ARCHIVED: "slate",
}

MANAGEMENT_STATUSES: Tuple[str, ...] = (
    PurchaseRequest.Status.APPROVED,
)

OVERVIEW_ALLOWED_STATUSES: Tuple[str, ...] = (
    PurchaseRequest.Status.DRAFT,
    PurchaseRequest.Status.SUBMITTED,
    PurchaseRequest.Status.RECEPTION,
)


@dataclass(frozen=True)
class PurchaseStatusSummary:
    status: str
    label: str
    theme: str
    count: int


@dataclass(frozen=True)
class PurchaseRequestListEntry:
    pk: int
    code: str
    name: str
    status: str
    status_label: str
    status_theme: str
    amount_label: str
    category_label: str
    supplier_label: str
    area_label: str
    updated_label: str
    stage_label: str
    stage_is_alert: bool
    items: Tuple[dict[str, object], ...]
    payment_details: dict[str, object]
    reception_details: dict[str, object]
    can_edit: bool
    edit_payload: Optional[dict[str, object]]


@dataclass(frozen=True)
class PurchaseRequestsOverview:
    entries: Tuple[PurchaseRequestListEntry, ...]
    total_count: int
    total_amount_label: str
    status_breakdown: Tuple[PurchaseStatusSummary, ...]


@dataclass(frozen=True)
class PurchaseManagementCard:
    purchase_id: int
    code: str
    name: str
    supplier_label: str
    area_label: str
    status: str
    status_label: str
    status_theme: str
    amount_label: str
    updated_label: str
    purchase_date_label: Optional[str]
    payment_condition_label: Optional[str]
    payment_method_label: Optional[str]
    delivery_condition_label: Optional[str]
    delivery_terms: Optional[str]
    bank_label: Optional[str]
    bank_account_label: Optional[str]
    payment_account_label: Optional[str]
    notes: Tuple[str, ...]
    allow_finalize: bool
    allow_modification: bool
    purchase_date_value: Optional[str]
    payment_condition_value: Optional[str]
    payment_method_value: Optional[str]
    delivery_condition_value: Optional[str]
    shipping_eta_value: Optional[str]
    shipping_notes: Optional[str]
    supplier_account_holder_id: Optional[str]
    supplier_account_holder_name: Optional[str]
    supplier_account_type: Optional[str]
    supplier_account_number: Optional[str]
    supplier_bank_name: Optional[str]
    payment_conditions: Tuple[dict[str, str], ...]
    payment_methods: Tuple[dict[str, str], ...]
    delivery_conditions: Tuple[dict[str, str], ...]
    account_types: Tuple[dict[str, str], ...]
    requires_bank_data: bool
    items: Tuple[dict[str, object], ...]


@dataclass(frozen=True)
class PurchaseApprovalEntry:
    purchase_id: int
    approval_id: int
    code: str
    name: str
    supplier_label: str
    area_label: str
    amount_label: str
    status_label: str
    status_theme: str
    role_label: str
    updated_label: str
    assigned_manager_id: Optional[int]
    items: Tuple[dict[str, object], ...]


@dataclass(frozen=True)
class PurchaseApprovalCard:
    entries: Tuple[PurchaseApprovalEntry, ...]
    manager_options: Tuple[dict[str, object], ...]


@dataclass(frozen=True)
class PurchaseRequestCategoryOption:
    id: int
    label: str
    support_document_type_id: Optional[int]


@dataclass(frozen=True)
class PurchaseRequestComposer:
    categories: Tuple[PurchaseRequestCategoryOption, ...]
    manager_options: Tuple[dict[str, object], ...]
    farms: Tuple[dict[str, object], ...]
    chicken_houses: Tuple[dict[str, object], ...]
    supplier_suggestions: Tuple[dict[str, object], ...]
    area_option_groups: Tuple[dict[str, object], ...]
    default_scope_value: str
    default_manager_id: Optional[int]



def build_purchase_requests_overview(*, user: Optional[UserProfile]) -> Optional[PurchaseRequestsOverview]:
    if not user or not getattr(user, "is_authenticated", False):
        return None

    user_id = getattr(user, "pk", None)
    if not user_id:
        return None

    queryset = (
        PurchaseRequest.objects.filter(
            Q(requester_id=user_id) | Q(assigned_manager_id=user_id),
            status__in=OVERVIEW_ALLOWED_STATUSES,
        )
        .select_related("expense_type", "supplier", "assigned_manager")
        .prefetch_related(
            Prefetch(
                "items",
                queryset=PurchaseItem.objects.select_related("product").order_by("pk"),
            )
        )
        .order_by("-updated_at")
    )
    entries: list[PurchaseRequestListEntry] = []
    currency = "COP"

    for purchase in queryset[:MAX_PURCHASE_ENTRIES]:
        currency = purchase.currency or currency
        amount_label = _format_currency(purchase.estimated_total or Decimal("0.00"), purchase.currency or currency)
        updated_label = _(
            "Actualizado %(date)s"
        ) % {
            "date": date_format(timezone.localtime(purchase.updated_at), "SHORT_DATETIME_FORMAT"),
        }
        payment_details = _build_payment_details(purchase=purchase, currency=currency)
        reception_details = _build_reception_details(purchase=purchase)
        item_payloads = tuple(_serialize_purchase_item(item=item, currency=purchase.currency or currency) for item in purchase.items.all())

        stage_label, stage_is_alert = _resolve_stage_label(purchase)
        can_edit = purchase.status == PurchaseRequest.Status.DRAFT
        entries.append(
            PurchaseRequestListEntry(
                pk=purchase.pk,
                code=purchase.timeline_code,
                name=purchase.name,
                status=purchase.status,
                status_label=purchase.get_status_display(),
                status_theme=PURCHASE_STATUS_THEME.get(purchase.status, "slate"),
                amount_label=amount_label,
                category_label=purchase.expense_type.name if purchase.expense_type else "",
                supplier_label=purchase.supplier.name if purchase.supplier else "",
                area_label=purchase.area_label,
                updated_label=updated_label,
                stage_label=stage_label,
                stage_is_alert=stage_is_alert,
                items=item_payloads,
                payment_details=payment_details,
                reception_details=reception_details,
                can_edit=can_edit,
                edit_payload=_build_purchase_edit_payload(purchase) if can_edit else None,
            )
        )

    aggregates = queryset.aggregate(
        total_amount=Sum("estimated_total"),
    )
    total_amount = aggregates.get("total_amount") or Decimal("0.00")
    status_breakdown = tuple(
        PurchaseStatusSummary(
            status=row["status"],
            label=PurchaseRequest.Status(row["status"]).label if row["status"] in PurchaseRequest.Status.values else row[
                "status"
            ],
            theme=PURCHASE_STATUS_THEME.get(row["status"], "slate"),
            count=row["count"],
        )
        for row in queryset.values("status")
        .order_by("status")
        .annotate(count=Count("pk"))
    )

    return PurchaseRequestsOverview(
        entries=tuple(entries),
        total_count=queryset.count(),
        total_amount_label=_format_currency(total_amount, currency),
        status_breakdown=status_breakdown,
    )


def build_purchase_management_card(*, user: Optional[UserProfile]) -> Optional[PurchaseManagementCard]:
    if not user or not getattr(user, "is_authenticated", False):
        return None

    purchase = (
        PurchaseRequest.objects.filter(assigned_manager=user, status__in=MANAGEMENT_STATUSES)
        .select_related("supplier")
        .prefetch_related(
            Prefetch(
                "items",
                queryset=PurchaseItem.objects.select_related("product").order_by("pk"),
            )
        )
        .order_by("status", "-updated_at")
        .first()
    )
    if not purchase:
        return None

    currency = purchase.currency or "COP"
    amount_label = _format_currency(purchase.estimated_total or Decimal("0.00"), currency)
    updated_label = date_format(timezone.localtime(purchase.updated_at), "SHORT_DATETIME_FORMAT")

    purchase_date_label = date_format(purchase.purchase_date, "DATE_FORMAT") if purchase.purchase_date else None
    delivery_condition_label = (
        PurchaseRequest.DeliveryCondition(purchase.delivery_condition).label
        if purchase.delivery_condition
        else None
    )
    payment_condition_label = (
        PurchaseRequest.PaymentCondition(purchase.payment_condition).label
        if purchase.payment_condition
        else None
    )
    payment_method_label = (
        PurchaseRequest.PaymentMethod(purchase.payment_method).label
        if purchase.payment_method
        else None
    )

    bank_label = purchase.supplier.bank_name if purchase.supplier and purchase.supplier.bank_name else None
    bank_account_label = (
        purchase.supplier.account_number if purchase.supplier and purchase.supplier.account_number else None
    )
    payment_account_label = purchase.payment_account or None
    notes: list[str] = []
    if purchase.shipping_notes:
        notes.append(purchase.shipping_notes.strip())
    if purchase.payment_notes:
        notes.append(purchase.payment_notes.strip())
    if purchase.reception_notes:
        notes.append(purchase.reception_notes.strip())

    allow_finalize = purchase.status == PurchaseRequest.Status.APPROVED
    allow_modification = purchase.status == PurchaseRequest.Status.APPROVED

    purchase_date_value = (
        purchase.purchase_date.isoformat() if purchase.purchase_date else timezone.localdate().isoformat()
    )
    shipping_eta_value = purchase.shipping_eta.isoformat() if purchase.shipping_eta else ""
    shipping_notes = purchase.shipping_notes or ""
    supplier_account_holder_id = (
        purchase.supplier_account_holder_id or (purchase.supplier.account_holder_id if purchase.supplier else "")
    )
    supplier_account_holder_name = (
        purchase.supplier_account_holder_name or (purchase.supplier.account_holder_name if purchase.supplier else "")
    )
    supplier_account_type = (
        purchase.supplier_account_type or (purchase.supplier.account_type if purchase.supplier else "")
    )
    supplier_account_number = (
        purchase.supplier_account_number or (purchase.supplier.account_number if purchase.supplier else "")
    )
    supplier_bank_name = purchase.supplier_bank_name or (purchase.supplier.bank_name if purchase.supplier else "")
    payment_conditions = tuple(
        {"id": value, "label": label} for value, label in PurchaseRequest.PaymentCondition.choices
    )
    payment_methods = tuple({"id": value, "label": label} for value, label in PurchaseRequest.PaymentMethod.choices)
    delivery_conditions = tuple(
        {"id": value, "label": label} for value, label in PurchaseRequest.DeliveryCondition.choices
    )
    account_types = tuple({"id": value, "label": label} for value, label in Supplier.ACCOUNT_TYPE_CHOICES)
    requires_bank_data = purchase.payment_method == PurchaseRequest.PaymentMethod.TRANSFER
    item_payloads = tuple(
        _serialize_purchase_item(item=item, currency=purchase.currency or currency) for item in purchase.items.all()
    )

    return PurchaseManagementCard(
        purchase_id=purchase.pk,
        code=purchase.timeline_code,
        name=purchase.name,
        supplier_label=purchase.supplier.name if purchase.supplier else "",
        area_label=purchase.area_label,
        status=purchase.status,
        status_label=purchase.get_status_display(),
        status_theme=PURCHASE_STATUS_THEME.get(purchase.status, "slate"),
        amount_label=amount_label,
        updated_label=updated_label,
        purchase_date_label=purchase_date_label,
        payment_condition_label=payment_condition_label,
        payment_method_label=payment_method_label,
        delivery_condition_label=delivery_condition_label,
        delivery_terms=purchase.delivery_terms or "",
        bank_label=bank_label,
        bank_account_label=bank_account_label,
        payment_account_label=payment_account_label,
        notes=tuple(note for note in notes if note),
        allow_finalize=allow_finalize,
        allow_modification=allow_modification,
        purchase_date_value=purchase_date_value,
        payment_condition_value=purchase.payment_condition or "",
        payment_method_value=purchase.payment_method or "",
        delivery_condition_value=purchase.delivery_condition or "",
        shipping_eta_value=shipping_eta_value,
        shipping_notes=shipping_notes,
        supplier_account_holder_id=supplier_account_holder_id,
        supplier_account_holder_name=supplier_account_holder_name,
        supplier_account_type=supplier_account_type,
        supplier_account_number=supplier_account_number,
        supplier_bank_name=supplier_bank_name,
        payment_conditions=payment_conditions,
        payment_methods=payment_methods,
        delivery_conditions=delivery_conditions,
        account_types=account_types,
        requires_bank_data=requires_bank_data,
        items=item_payloads,
    )


def build_purchase_approval_card(*, user: Optional[UserProfile]) -> Optional[PurchaseApprovalCard]:
    if not user or not getattr(user, "is_authenticated", False):
        return None

    approvals = (
        PurchaseApproval.objects.filter(
            approver=user,
            status=PurchaseApproval.Status.PENDING,
        )
        .select_related(
            "purchase_request",
            "purchase_request__supplier",
            "purchase_request__assigned_manager",
        )
        .prefetch_related(
            Prefetch(
                "purchase_request__items",
                queryset=PurchaseItem.objects.select_related("product").order_by("pk"),
            )
        )
        .order_by("sequence", "-purchase_request__updated_at")
    )
    entries: list[PurchaseApprovalEntry] = []

    for approval in approvals[:MAX_APPROVAL_ENTRIES]:
        purchase = approval.purchase_request
        if not purchase:
            continue
        currency = purchase.currency or "COP"
        amount_label = _format_currency(purchase.estimated_total or Decimal("0.00"), currency)
        updated_label = _(
            "Actualizado %(date)s"
        ) % {
            "date": date_format(timezone.localtime(purchase.updated_at), "SHORT_DATETIME_FORMAT"),
        }
        manager_id = _resolve_default_manager_id(purchase=purchase, fallback_user=user)
        item_payloads = tuple(
            _serialize_purchase_item(item=item, currency=purchase.currency or currency) for item in purchase.items.all()
        )
        entries.append(
            PurchaseApprovalEntry(
                purchase_id=purchase.pk,
                approval_id=approval.pk,
                code=purchase.timeline_code,
                name=purchase.name,
                supplier_label=purchase.supplier.name if purchase.supplier else "",
                area_label=purchase.area_label,
                amount_label=amount_label,
                status_label=purchase.get_status_display(),
                status_theme=PURCHASE_STATUS_THEME.get(purchase.status, "slate"),
                role_label=approval.role or _("Aprobador"),
                updated_label=updated_label,
                assigned_manager_id=manager_id,
                items=item_payloads,
            )
        )

    if not entries:
        return None

    manager_options = _build_manager_options()

    return PurchaseApprovalCard(entries=tuple(entries), manager_options=manager_options)



def serialize_purchase_requests_overview(card: PurchaseRequestsOverview) -> dict[str, object]:
    return {
        "title": _("Mis compras"),
        "subtitle": _("Revisa rápidamente el estado de las compras que has creado."),
        "summary": {
            "total_amount": card.total_amount_label,
            "total_count": card.total_count,
            "status_breakdown": [
                {
                    "id": summary.status,
                    "label": summary.label,
                    "count": summary.count,
                    "theme": summary.theme,
                }
                for summary in card.status_breakdown
            ],
        },
        "entries": [
            {
                "id": entry.pk,
                "code": entry.code,
                "name": entry.name,
                "status": entry.status,
                "status_label": entry.status_label,
                "status_theme": entry.status_theme,
                "amount_label": entry.amount_label,
                "category_label": entry.category_label,
                "supplier_label": entry.supplier_label,
                "area_label": entry.area_label,
                "updated_label": entry.updated_label,
                "stage_label": entry.stage_label,
                "stage_is_alert": entry.stage_is_alert,
                "items": list(entry.items),
                "payment_details": entry.payment_details,
                "reception_details": entry.reception_details,
                "can_edit": entry.can_edit,
                "edit_payload": entry.edit_payload,
            }
            for entry in card.entries
        ],
        "empty_state": {
            "title": _("Aún no tienes solicitudes"),
            "description": _(
                "Registra una nueva solicitud de compra para ver el estado y las aprobaciones directamente desde aquí."
            ),
        },
    }


def serialize_purchase_management_card(card: PurchaseManagementCard) -> dict[str, object]:
    return {
        "has_purchase": True,
        "purchase": {
            "id": card.purchase_id,
            "code": card.code,
            "name": card.name,
            "supplier_label": card.supplier_label,
            "area_label": card.area_label,
            "status_label": card.status_label,
            "status_theme": card.status_theme,
            "amount_label": card.amount_label,
            "updated_label": card.updated_label,
            "purchase_date_label": card.purchase_date_label,
            "payment_condition_label": card.payment_condition_label,
            "payment_method_label": card.payment_method_label,
            "delivery_condition_label": card.delivery_condition_label,
            "delivery_terms": card.delivery_terms,
            "bank_label": card.bank_label,
            "bank_account_label": card.bank_account_label,
            "payment_account_label": card.payment_account_label,
            "notes": list(card.notes),
            "items": list(card.items),
            "form": {
                "purchase_date": card.purchase_date_value,
                "payment_condition": card.payment_condition_value,
                "payment_method": card.payment_method_value,
                "delivery_condition": card.delivery_condition_value,
                "shipping_eta": card.shipping_eta_value,
                "shipping_notes": card.shipping_notes,
                "supplier_account_holder_id": card.supplier_account_holder_id or "",
                "supplier_account_holder_name": card.supplier_account_holder_name or "",
                "supplier_account_type": card.supplier_account_type or "",
                "supplier_account_number": card.supplier_account_number or "",
                "supplier_bank_name": card.supplier_bank_name or "",
                "payment_conditions": list(card.payment_conditions),
                "payment_methods": list(card.payment_methods),
                "delivery_conditions": list(card.delivery_conditions),
                "account_types": list(card.account_types),
                "requires_bank_data": card.requires_bank_data,
            },
        },
        "allow_finalize": card.allow_finalize,
        "allow_modification": card.allow_modification,
    }


def serialize_purchase_management_empty_state() -> dict[str, object]:
    return {
        "has_purchase": False,
        "message": _("No tienes solicitudes aprobadas pendientes de gestionar en este momento."),
    }


def serialize_purchase_approval_card(card: PurchaseApprovalCard) -> dict[str, object]:
    return {
        "entries": [
            {
                "id": entry.purchase_id,
                "approval_id": entry.approval_id,
                "code": entry.code,
                "name": entry.name,
                "supplier_label": entry.supplier_label,
                "area_label": entry.area_label,
                "amount_label": entry.amount_label,
                "status_label": entry.status_label,
                "status_theme": entry.status_theme,
                "role_label": entry.role_label,
                "updated_label": entry.updated_label,
                "assigned_manager_id": entry.assigned_manager_id,
                "items": list(entry.items),
            }
            for entry in card.entries
        ],
        "manager_options": list(card.manager_options),
    }


def serialize_purchase_request_composer(card: PurchaseRequestComposer) -> dict[str, object]:
    return {
        "title": _("Solicitar compra"),
        "subtitle": _(
            "Abre múltiples solicitudes en paralelo, captura los ítems y envíalas a aprobación en el mismo flujo."
        ),
        "max_items": MAX_PURCHASE_REQUEST_ITEMS,
        "defaults": {
            "summary": "",
            "notes": "",
            "assigned_manager_id": card.default_manager_id,
        },
        "categories": [
            {
                "id": option.id,
                "label": option.label,
                "support_document_type_id": option.support_document_type_id,
            }
            for option in card.categories
        ],
        "manager_options": list(card.manager_options),
        "farms": list(card.farms),
        "chicken_houses": list(card.chicken_houses),
        "supplier_suggestions": list(card.supplier_suggestions),
        "area_option_groups": list(card.area_option_groups),
        "default_scope_value": card.default_scope_value,
    }


def build_purchase_request_composer(*, user: Optional[UserProfile]) -> Optional[PurchaseRequestComposer]:
    if not user or not getattr(user, "is_authenticated", False):
        return None

    categories = tuple(
        PurchaseRequestCategoryOption(
            id=category.pk,
            label=category.name,
            support_document_type_id=category.default_support_document_type_id,
        )
        for category in PurchasingExpenseType.objects.only("id", "name", "default_support_document_type")
        .order_by("name")
    )
    if not categories:
        return None

    manager_options = _build_manager_options()
    farms = tuple(
        {
            "id": farm["id"],
            "label": farm["name"],
        }
        for farm in Farm.objects.order_by("name").values("id", "name")
    )
    chicken_houses = tuple(
        {
            "id": house["id"],
            "label": house["name"],
            "farm_id": house["farm_id"],
            "full_label": f"{house['farm__name']} · {house['name']}",
            "farm_name": house["farm__name"],
        }
        for house in ChickenHouse.objects.select_related("farm")
        .order_by("farm__name", "name")
        .values("id", "name", "farm_id", "farm__name")
    )
    supplier_suggestions = tuple(
        {
            "id": supplier["id"],
            "label": supplier["name"],
            "tax_id": supplier.get("tax_id") or "",
            "city": supplier.get("city") or "",
        }
        for supplier in Supplier.objects.order_by("name")
        .values("id", "name", "tax_id", "city")[:RECENT_SUPPLIER_SUGGESTIONS]
    )

    area_option_groups = tuple(_build_area_option_groups_data(farms=farms, chicken_houses=chicken_houses))

    return PurchaseRequestComposer(
        categories=categories,
        manager_options=manager_options,
        farms=farms,
        chicken_houses=chicken_houses,
        supplier_suggestions=supplier_suggestions,
        area_option_groups=area_option_groups,
        default_scope_value=PurchaseRequest.AreaScope.COMPANY,
        default_manager_id=getattr(user, "pk", None),
    )


def _build_manager_options() -> Tuple[dict[str, object], ...]:
    return tuple(
        {
            "id": profile.pk,
            "label": profile.get_full_name() or profile.get_username() or _("Sin nombre"),
        }
        for profile in UserProfile.objects.only("id", "nombres", "apellidos", "cedula")
        .order_by("apellidos", "nombres", "id")
    )


def _build_area_option_groups_data(*, farms: Sequence[Mapping[str, object]], chicken_houses: Sequence[Mapping[str, object]]) -> Sequence[dict[str, object]]:
    groups: list[dict[str, object]] = [
        {
            "label": _("General"),
            "options": [
                {
                    "value": PurchaseRequest.AreaScope.COMPANY,
                    "label": _("Empresa"),
                    "description": _("Gasto corporativo"),
                }
            ],
        }
    ]
    if farms:
        groups.append(
            {
                "label": _("Granjas"),
                "options": [
                    {
                        "value": f"{PurchaseRequest.AreaScope.FARM}:{farm['id']}",
                        "label": farm.get("label") or "",
                        "description": _("Granja"),
                    }
                    for farm in farms
                ],
            }
        )
    if chicken_houses:
        groups.append(
            {
                "label": _("Galpones"),
                "options": [
                    {
                        "value": f"{PurchaseRequest.AreaScope.CHICKEN_HOUSE}:{house['id']}",
                        "label": house.get("full_label") or house.get("label") or "",
                        "description": _("Galpón"),
                    }
                    for house in chicken_houses
                ],
            }
        )
    return groups


def _resolve_default_manager_id(*, purchase: PurchaseRequest, fallback_user: Optional[UserProfile]) -> Optional[int]:
    if purchase.assigned_manager_id:
        return purchase.assigned_manager_id
    if purchase.requester_id:
        return purchase.requester_id
    if fallback_user and getattr(fallback_user, "pk", None):
        return fallback_user.pk
    return None


def _format_currency(amount: Decimal, currency: str) -> str:
    amount = amount or Decimal("0.00")
    quantized = amount.quantize(Decimal("0.01"))
    symbol = _currency_symbol(currency)
    return f"{symbol} {quantized:,.2f}"


def _resolve_stage_label(purchase: PurchaseRequest) -> tuple[str, bool]:
    if purchase.status == PurchaseRequest.Status.RECEPTION:
        if purchase.reception_mismatch:
            return _("Recepción con diferencias"), True
        if _has_pending_reception_items(purchase):
            return _("Esperando llegada"), False
        return _("Recibida"), False
    stage_map: dict[str, str] = {
        PurchaseRequest.Status.DRAFT: _("Borrador"),
        PurchaseRequest.Status.SUBMITTED: _("En aprobación"),
        PurchaseRequest.Status.APPROVED: _("En Gestión"),
        PurchaseRequest.Status.INVOICE: _("Soportes"),
        PurchaseRequest.Status.PAYMENT: _("Pago"),
        PurchaseRequest.Status.ARCHIVED: _("Archivada"),
    }
    return stage_map.get(purchase.status, purchase.status), False


def _serialize_purchase_item(*, item: PurchaseItem, currency: str) -> dict[str, object]:
    requested_label = _format_quantity(item.quantity)
    received_label = _format_quantity(item.received_quantity)
    unit_value = _resolve_unit_value(item)
    subtotal_value = (item.quantity or Decimal("0.00")) * unit_value
    unit_value_label = _format_currency(unit_value, currency)
    subtotal_label = _format_currency(subtotal_value, currency)
    return {
        "id": item.pk,
        "description": item.description,
        "product_label": item.product.name if item.product else "",
        "requested_label": requested_label,
        "received_label": received_label,
        "unit_value_label": unit_value_label,
        "subtotal_label": subtotal_label,
        "quantity_label": requested_label,
        "amount_label": subtotal_label,
        "scope_value": item.scope_value(),
        "scope_label": item.area_label,
    }


def _build_purchase_edit_payload(purchase: PurchaseRequest) -> dict[str, object]:
    supplier_payload: Optional[dict[str, object]] = None
    if purchase.supplier_id:
        supplier_payload = {
            "id": purchase.supplier_id,
            "name": purchase.supplier.name if purchase.supplier else "",
            "tax_id": purchase.supplier.tax_id if purchase.supplier else "",
            "city": purchase.supplier.city if purchase.supplier else "",
        }

    manager_label = ""
    if purchase.assigned_manager_id and purchase.assigned_manager:
        manager_label = (
            purchase.assigned_manager.get_full_name()
            or purchase.assigned_manager.get_username()
            or _("Sin nombre")
        )

    items_payload: list[dict[str, object]] = []
    total = Decimal("0.00")
    for item in purchase.items.all():
        quantity = item.quantity or Decimal("0.00")
        unit_value = _resolve_unit_value(item)
        subtotal = (quantity or Decimal("0.00")) * unit_value
        total += subtotal
        items_payload.append(
            {
                "id": item.pk,
                "description": item.description,
                "product_id": item.product_id,
                "quantity": _format_quantity(quantity),
                "unit_value": _format_decimal_input(unit_value),
                "subtotal": _format_decimal_input(subtotal),
                "scope_value": item.scope_value(),
                "scope_label": item.area_label,
            }
        )

    return {
        "purchase_id": purchase.pk,
        "code": purchase.timeline_code,
        "name": purchase.name,
        "expense_type_id": purchase.expense_type_id,
        "assigned_manager_id": purchase.assigned_manager_id,
        "assigned_manager_label": manager_label,
        "notes": purchase.description or "",
        "supplier": supplier_payload,
        "items": items_payload,
        "total_value": _format_decimal_input(total),
        "revision_notes": _extract_revision_notes(purchase.shipping_notes),
    }


def _format_decimal_input(value: Decimal | None) -> str:
    if value is None:
        return ""
    normalized = value.quantize(Decimal("0.01"))
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".") or "0"
    return text


def _extract_revision_notes(raw_notes: Optional[str]) -> list[str]:
    if not raw_notes:
        return []
    entries: list[str] = []
    for chunk in raw_notes.splitlines():
        line = chunk.strip()
        if line:
            entries.append(line)
    return entries


def _format_quantity(value: Decimal | None) -> str:
    if value is None:
        return "0"
    normalized = value.normalize()
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _resolve_unit_value(item: PurchaseItem) -> Decimal:
    return (item.estimated_amount or Decimal("0")).quantize(Decimal("0.01"))


def _format_date(value) -> str:
    if not value:
        return ""
    return date_format(value, "DATE_FORMAT")


def _build_payment_details(*, purchase: PurchaseRequest, currency: str) -> dict[str, object]:
    return {
        "payment_method_label": (
            PurchaseRequest.PaymentMethod(purchase.payment_method).label if purchase.payment_method else ""
        ),
        "payment_condition_label": (
            PurchaseRequest.PaymentCondition(purchase.payment_condition).label if purchase.payment_condition else ""
        ),
        "payment_amount_label": _format_currency(purchase.payment_amount or Decimal("0.00"), currency),
        "payment_account_label": purchase.payment_account or "",
        "payment_date_label": _format_date(purchase.payment_date) or "",
    }


def _build_reception_details(*, purchase: PurchaseRequest) -> dict[str, object]:
    status_hint = ""
    if purchase.reception_mismatch:
        status_hint = _("Cantidades recibidas distintas a las solicitadas. Revisa la recepción antes de continuar.")
    elif purchase.status == PurchaseRequest.Status.RECEPTION and not _has_pending_reception_items(purchase):
        status_hint = _("Recepción completa registrada.")
    return {
        "delivery_condition_label": (
            PurchaseRequest.DeliveryCondition(purchase.delivery_condition).label if purchase.delivery_condition else ""
        ),
        "shipping_eta_label": _format_date(purchase.shipping_eta) or "",
        "shipping_notes": (purchase.shipping_notes or "").strip(),
        "reception_notes": (purchase.reception_notes or "").strip(),
        "status_hint": status_hint,
    }


def _has_pending_reception_items(purchase: PurchaseRequest) -> bool:
    for item in purchase.items.all():
        requested = item.quantity or Decimal("0")
        received = item.received_quantity or Decimal("0")
        if received < requested:
            return True
    return False


def _currency_symbol(currency: Optional[str]) -> str:
    if not currency:
        return "$"
    code = currency.upper()
    if code == "COP":
        return "$"
    return code
    items: Tuple[dict[str, object], ...]

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Tuple

from django.db.models import Count, Prefetch, Sum
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _

from administration.models import PurchaseRequest, PurchasingExpenseType, Supplier
from administration.models import Product as AdministrationProduct, PurchaseItem
from production.models import ChickenHouse, Farm
from personal.models import UserProfile


MAX_SUPPLIERS = 60
MAX_PRODUCTS = 60
MAX_PURCHASE_ENTRIES = 6

REQUEST_SCOPE_HELPERS: dict[str, str] = {
    PurchaseRequest.AreaScope.COMPANY: _("Úsalo para compras corporativas o multi-granja."),
    PurchaseRequest.AreaScope.FARM: _("Asocia la solicitud a una granja específica."),
    PurchaseRequest.AreaScope.CHICKEN_HOUSE: _("Detalla el galpón y lote para agilizar la entrega."),
}

PURCHASE_STATUS_THEME: dict[str, str] = {
    PurchaseRequest.Status.DRAFT: "slate",
    PurchaseRequest.Status.SUBMITTED: "amber",
    PurchaseRequest.Status.APPROVED: "brand",
    PurchaseRequest.Status.ORDERED: "indigo",
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
    PurchaseRequest.Status.ORDERED,
)


@dataclass(frozen=True)
class PurchaseRequestFormCard:
    submit_url: str
    currency: str
    expense_types: Tuple[dict[str, object], ...]
    suppliers: Tuple[dict[str, object], ...]
    products: Tuple[dict[str, object], ...]
    farms: Tuple[dict[str, object], ...]
    chicken_houses: Tuple[dict[str, object], ...]
    area_scopes: Tuple[dict[str, object], ...]
    defaults: dict[str, object]
    messages: dict[str, str]
    max_items: int = MAX_PURCHASE_ENTRIES


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
    items: Tuple[dict[str, object], ...]
    payment_details: dict[str, object]
    reception_details: dict[str, object]


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


def build_purchase_request_form_card(
    *,
    user: Optional[UserProfile],
    submit_url: str,
) -> Optional[PurchaseRequestFormCard]:
    if not user or not getattr(user, "is_authenticated", False):
        return None

    expense_queryset = PurchasingExpenseType.objects.order_by("name").prefetch_related("approval_rules__approver")
    expense_types = tuple(
        {
            "id": expense.pk,
            "name": expense.name,
            "approval_summary": expense.approval_phase_summary,
        }
        for expense in expense_queryset
    )

    suppliers = tuple(
        {
            "id": supplier.pk,
            "name": supplier.name,
            "tax_id": supplier.tax_id,
            "city": supplier.city or "",
            "has_bank_data": bool(supplier.bank_name and supplier.account_number),
        }
        for supplier in Supplier.objects.only(
            "id",
            "name",
            "tax_id",
            "city",
            "bank_name",
            "account_number",
        )
        .order_by("name")[:MAX_SUPPLIERS]
    )

    products = tuple(
        {
            "id": product.pk,
            "name": product.name,
            "unit": product.get_unit_display(),
        }
        for product in AdministrationProduct.objects.only("id", "name", "unit")
        .order_by("name")[:MAX_PRODUCTS]
    )

    farms = tuple(
        {
            "id": farm.pk,
            "name": farm.name,
        }
        for farm in Farm.objects.only("id", "name").order_by("name")
    )

    chicken_houses = tuple(
        {
            "id": house.pk,
            "name": house.name,
            "farm_id": house.farm_id,
            "farm_name": house.farm.name if house.farm_id else "",
        }
        for house in ChickenHouse.objects.select_related("farm")
        .only("id", "name", "farm_id", "farm__name")
        .order_by("name")
    )

    area_scopes = tuple(
        {
            "id": scope.value,
            "label": scope.label,
            "helper": REQUEST_SCOPE_HELPERS.get(scope.value, ""),
        }
        for scope in PurchaseRequest.AreaScope
    )

    defaults = {
        "scope": PurchaseRequest.AreaScope.COMPANY,
    }

    messages = {
        "items_helper": _(
            "Describe los ítems en lenguaje sencillo. Puedes combinar descripción libre y el listado de productos sugeridos."
        ),
        "suppliers_helper": _("Selecciona un proveedor existente o regístralo sin salir de la mini app."),
    }

    return PurchaseRequestFormCard(
        submit_url=submit_url,
        currency="COP",
        expense_types=expense_types,
        suppliers=suppliers,
        products=products,
        farms=farms,
        chicken_houses=chicken_houses,
        area_scopes=area_scopes,
        defaults=defaults,
        messages=messages,
    )


def build_purchase_requests_overview(*, user: Optional[UserProfile]) -> Optional[PurchaseRequestsOverview]:
    if not user or not getattr(user, "is_authenticated", False):
        return None

    queryset = (
        PurchaseRequest.objects.filter(requester=user, status__in=OVERVIEW_ALLOWED_STATUSES)
        .select_related("expense_type", "supplier")
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
                stage_label=_resolve_stage_label(purchase.status),
                items=item_payloads,
                payment_details=payment_details,
                reception_details=reception_details,
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
        PurchaseRequest.objects.filter(requester=user, status__in=MANAGEMENT_STATUSES)
        .select_related("supplier")
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
    allow_modification = purchase.status in {
        PurchaseRequest.Status.APPROVED,
        PurchaseRequest.Status.ORDERED,
    }

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
    )


def serialize_purchase_request_form_card(card: PurchaseRequestFormCard) -> dict[str, object]:
    return {
        "submit_url": card.submit_url,
        "currency": card.currency,
        "currency_symbol": _currency_symbol(card.currency),
        "max_items": card.max_items,
        "expense_types": list(card.expense_types),
        "suppliers": list(card.suppliers),
        "products": list(card.products),
        "farms": list(card.farms),
        "chicken_houses": list(card.chicken_houses),
        "area_scopes": list(card.area_scopes),
        "defaults": card.defaults,
        "messages": card.messages,
    }


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
                "items": list(entry.items),
                "payment_details": entry.payment_details,
                "reception_details": entry.reception_details,
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
        },
        "allow_finalize": card.allow_finalize,
        "allow_modification": card.allow_modification,
    }


def serialize_purchase_management_empty_state() -> dict[str, object]:
    return {
        "has_purchase": False,
        "message": _("No tienes solicitudes aprobadas pendientes de gestionar en este momento."),
    }


def _format_currency(amount: Decimal, currency: str) -> str:
    amount = amount or Decimal("0.00")
    quantized = amount.quantize(Decimal("0.01"))
    symbol = _currency_symbol(currency)
    return f"{symbol} {quantized:,.2f}"


def _resolve_stage_label(status: str) -> str:
    stage_map: dict[str, str] = {
        PurchaseRequest.Status.DRAFT: _("Borrador"),
        PurchaseRequest.Status.SUBMITTED: _("En aprobación"),
        PurchaseRequest.Status.APPROVED: _("En Gestión"),
        PurchaseRequest.Status.ORDERED: _("Orden emitida"),
        PurchaseRequest.Status.RECEPTION: _("Esperando llegada"),
        PurchaseRequest.Status.INVOICE: _("Soportes"),
        PurchaseRequest.Status.PAYMENT: _("Pago"),
        PurchaseRequest.Status.ARCHIVED: _("Archivada"),
    }
    return stage_map.get(status, status)


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
    }


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
    return {
        "delivery_condition_label": (
            PurchaseRequest.DeliveryCondition(purchase.delivery_condition).label if purchase.delivery_condition else ""
        ),
        "shipping_eta_label": _format_date(purchase.shipping_eta) or "",
        "shipping_notes": (purchase.shipping_notes or "").strip(),
        "reception_notes": (purchase.reception_notes or "").strip(),
    }


def _currency_symbol(currency: Optional[str]) -> str:
    if not currency:
        return "$"
    code = currency.upper()
    if code == "COP":
        return "$"
    return code

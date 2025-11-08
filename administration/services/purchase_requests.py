from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import re
from typing import Iterable, Sequence

from django.db import IntegrityError, transaction
from django.utils import timezone

from administration.models import (
    PurchaseItem,
    PurchaseRequest,
    PurchasingExpenseType,
    Supplier,
    SupportDocumentType,
)
from administration.services.workflows import PurchaseApprovalWorkflowService
from production.models import ChickenHouse, Farm


@dataclass
class PurchaseItemPayload:
    id: int | None
    description: str
    quantity: Decimal
    estimated_amount: Decimal


@dataclass
class PurchaseRequestPayload:
    purchase_id: int | None
    summary: str
    notes: str
    expense_type_id: int | None
    support_document_type_id: int | None
    supplier_id: int | None
    items: Sequence[PurchaseItemPayload]
    scope_farm_id: int | None
    scope_chicken_house_id: int | None
    scope_batch_code: str


class PurchaseRequestValidationError(Exception):
    def __init__(
        self,
        *,
        field_errors: dict[str, list[str]] | None = None,
        item_errors: dict[int, dict[str, list[str]]] | None = None,
    ) -> None:
        super().__init__("Invalid purchase request data")
        self.field_errors = field_errors or {}
        self.item_errors = item_errors or {}


def generate_timeline_code() -> str:
    prefix = timezone.now().strftime("SOL-%Y")
    pattern = f"{prefix}-"
    last_code = (
        PurchaseRequest.objects.filter(timeline_code__startswith=pattern)
        .order_by("-timeline_code")
        .values_list("timeline_code", flat=True)
        .first()
    )
    next_number = 1
    if last_code:
        match = re.search(r"(\d+)$", last_code)
        if match:
            next_number = int(match.group(1)) + 1
    return f"{pattern}{next_number:04d}"


class PurchaseRequestSubmissionService:
    def __init__(self, *, actor):
        self.actor = actor

    def submit(self, *, payload: PurchaseRequestPayload, intent: str) -> PurchaseRequest:
        intent = intent or "save_draft"
        if intent not in {"save_draft", "send_workflow"}:
            raise PurchaseRequestValidationError(
                field_errors={"non_field": ["Acción no soportada para la solicitud."]}
            )

        with transaction.atomic():
            purchase = self._load_purchase(payload.purchase_id)
            self._validate_payload(payload, purchase)
            purchase = self._persist_purchase(purchase, payload)
            total = self._sync_items(purchase, payload.items)
            if purchase.estimated_total != total:
                purchase.estimated_total = total
                purchase.save(update_fields=["estimated_total", "updated_at"])
            if intent == "save_draft":
                purchase.status = PurchaseRequest.Status.DRAFT
                purchase.save(update_fields=["status", "updated_at"])
                purchase.refresh_from_db()
                return purchase
            PurchaseApprovalWorkflowService(
                purchase_request=purchase,
                actor=self.actor,
            ).run()
            purchase.refresh_from_db()
            return purchase

    def _load_purchase(self, purchase_id: int | None) -> PurchaseRequest | None:
        if not purchase_id:
            return None
        try:
            purchase = (
                PurchaseRequest.objects.select_for_update()
                .select_related("expense_type", "supplier")
                .get(pk=purchase_id)
            )
        except PurchaseRequest.DoesNotExist as exc:
            raise PurchaseRequestValidationError(
                field_errors={"non_field": ["La solicitud seleccionada ya no existe."]}
            ) from exc
        return purchase

    def _validate_payload(
        self,
        payload: PurchaseRequestPayload,
        purchase: PurchaseRequest | None,
    ) -> None:
        field_errors: dict[str, list[str]] = {}
        item_errors: dict[int, dict[str, list[str]]] = {}

        expense_type = None
        if not payload.summary:
            field_errors["summary"] = ["Ingresa un nombre para la solicitud."]
        if not payload.expense_type_id:
            field_errors["expense_type"] = ["Selecciona una categoría."]
        else:
            expense_type = (
                PurchasingExpenseType.objects.filter(pk=payload.expense_type_id)
                .only("id", "default_support_document_type_id")
                .first()
            )
            if not expense_type:
                field_errors["expense_type"] = ["La categoría seleccionada no está disponible."]

        supplier = None
        if not payload.supplier_id:
            field_errors["supplier"] = ["Selecciona un proveedor."]
        else:
            supplier = Supplier.objects.filter(pk=payload.supplier_id).only("id").first()
            if not supplier:
                field_errors["supplier"] = ["El proveedor seleccionado ya no existe."]

        farm = None
        if payload.scope_farm_id:
            farm = Farm.objects.filter(pk=payload.scope_farm_id).first()
            if not farm:
                field_errors["scope_farm_id"] = ["Selecciona una granja válida."]

        house = None
        if payload.scope_chicken_house_id:
            house = (
                ChickenHouse.objects.select_related("farm")
                .filter(pk=payload.scope_chicken_house_id)
                .first()
            )
            if not house:
                field_errors["scope_chicken_house_id"] = ["Selecciona un galpón válido."]
            elif farm and house.farm_id != farm.id:
                field_errors["scope_chicken_house_id"] = ["El galpón debe pertenecer a la granja seleccionada."]
            elif not farm and house.farm:
                payload.scope_farm_id = house.farm_id
                farm = house.farm

        if payload.support_document_type_id:
            if not SupportDocumentType.objects.filter(pk=payload.support_document_type_id).only("id").exists():
                field_errors["support_document_type"] = ["Selecciona un tipo de soporte válido."]
        elif expense_type and expense_type.default_support_document_type_id:
            payload.support_document_type_id = expense_type.default_support_document_type_id

        if purchase and purchase.status != PurchaseRequest.Status.DRAFT:
            field_errors.setdefault(
                "non_field",
                ["Solo puedes editar solicitudes que estén en borrador."],
            )

        if not payload.items:
            field_errors["items"] = ["Agrega al menos un item a la solicitud."]
        else:
            self._validate_items(payload.items, item_errors)

        if field_errors or item_errors:
            raise PurchaseRequestValidationError(
                field_errors=field_errors,
                item_errors=item_errors,
            )

    def _validate_items(
        self,
        items: Sequence[PurchaseItemPayload],
        item_errors: dict[int, dict[str, list[str]]],
    ) -> None:
        for index, item in enumerate(items):
            errors_for_row: dict[str, list[str]] = {}
            if not item.description:
                errors_for_row["description"] = ["La descripción es obligatoria."]
            if item.quantity <= 0:
                errors_for_row["quantity"] = ["La cantidad debe ser mayor que cero."]
            if item.estimated_amount < 0:
                errors_for_row["estimated_amount"] = ["El monto estimado no puede ser negativo."]
            if errors_for_row:
                item_errors[index] = errors_for_row

    def _persist_purchase(
        self,
        purchase: PurchaseRequest | None,
        payload: PurchaseRequestPayload,
    ) -> PurchaseRequest:
        is_new = purchase is None
        purchase = purchase or PurchaseRequest()
        purchase.name = payload.summary
        purchase.description = payload.notes
        purchase.expense_type_id = payload.expense_type_id
        purchase.supplier_id = payload.supplier_id
        purchase.support_document_type_id = payload.support_document_type_id
        purchase.scope_farm_id = payload.scope_farm_id
        purchase.scope_chicken_house_id = payload.scope_chicken_house_id
        purchase.scope_batch_code = payload.scope_batch_code
        if not purchase.requester_id and getattr(self.actor, "is_authenticated", False):
            purchase.requester = self.actor
        purchase.status = PurchaseRequest.Status.DRAFT
        purchase.currency = purchase.currency or "COP"
        self._save_with_code(purchase, force_code=is_new)
        return purchase

    def _save_with_code(self, purchase: PurchaseRequest, *, force_code: bool) -> None:
        attempts = 0
        while True:
            try:
                if force_code or not purchase.timeline_code:
                    purchase.timeline_code = generate_timeline_code()
                purchase.save()
                break
            except IntegrityError:
                attempts += 1
                if attempts >= 5:
                    raise PurchaseRequestValidationError(
                        field_errors={"non_field": ["No fue posible generar un código de solicitud único."]},
                    )

    def _sync_items(
        self,
        purchase: PurchaseRequest,
        items: Iterable[PurchaseItemPayload],
    ) -> Decimal:
        existing = {item.id: item for item in purchase.items.all()}
        seen_ids: set[int] = set()
        total = Decimal("0.00")
        for item_payload in items:
            item = existing.get(item_payload.id) if item_payload.id in existing else None
            if item is None:
                item = PurchaseItem(purchase=purchase)
            item.description = item_payload.description
            item.quantity = item_payload.quantity
            item.estimated_amount = item_payload.estimated_amount
            item.save()
            if item.id:
                seen_ids.add(item.id)
            total += item_payload.estimated_amount
        for item_id, item in existing.items():
            if item_id not in seen_ids:
                item.delete()
        return total

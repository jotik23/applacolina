from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Sequence

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from administration.models import PurchaseItem, PurchaseReceptionAttachment, PurchaseRequest


@dataclass
class ReceptionItemPayload:
    item_id: int
    received_quantity: Decimal


@dataclass
class PurchaseReceptionPayload:
    purchase_id: int
    notes: str
    items: Sequence[ReceptionItemPayload]


class PurchaseReceptionValidationError(Exception):
    def __init__(self, *, field_errors: dict[str, list[str]] | None = None, item_errors: dict[int, list[str]] | None = None) -> None:
        super().__init__("Invalid reception payload")
        self.field_errors = field_errors or {}
        self.item_errors = item_errors or {}


class PurchaseReceptionService:
    def __init__(self, *, actor) -> None:
        self.actor = actor

    def register(
        self,
        *,
        payload: PurchaseReceptionPayload,
        intent: str,
        attachments: Iterable,
    ) -> PurchaseRequest:
        with transaction.atomic():
            purchase = self._load_purchase(payload.purchase_id)
            field_errors, item_errors = self._validate(payload, purchase)
            if field_errors or item_errors:
                raise PurchaseReceptionValidationError(field_errors=field_errors, item_errors=item_errors)
            self._persist_reception(purchase, payload, intent=intent)
            self._persist_attachments(purchase, attachments)
            return purchase

    def _load_purchase(self, purchase_id: int) -> PurchaseRequest:
        return (
            PurchaseRequest.objects.select_for_update()
            .prefetch_related("items")
            .get(pk=purchase_id)
        )

    def _validate(
        self,
        payload: PurchaseReceptionPayload,
        purchase: PurchaseRequest,
    ) -> tuple[dict[str, list[str]], dict[int, list[str]]]:
        field_errors: dict[str, list[str]] = {}
        item_errors: dict[int, list[str]] = {}
        items_by_id = {item.id: item for item in purchase.items.all()}
        if not payload.items:
            field_errors.setdefault("non_field", []).append("Debes registrar al menos un item.")
        for index, item_payload in enumerate(payload.items):
            errors: list[str] = []
            purchase_item = items_by_id.get(item_payload.item_id)
            if not purchase_item:
                errors.append("El item seleccionado no existe.")
            elif item_payload.received_quantity < Decimal("0"):
                errors.append("La cantidad recibida no puede ser negativa.")
            if errors:
                item_errors[index] = errors
        return field_errors, item_errors

    def _persist_reception(self, purchase: PurchaseRequest, payload: PurchaseReceptionPayload, *, intent: str) -> None:
        items_by_id = {item.id: item for item in purchase.items.all()}
        items_to_update: list[PurchaseItem] = []
        for item_payload in payload.items:
            purchase_item = items_by_id.get(item_payload.item_id)
            if not purchase_item:
                continue
            purchase_item.received_quantity = item_payload.received_quantity
            purchase_item.updated_at = timezone.now()
            items_to_update.append(purchase_item)
        if items_to_update:
            PurchaseItem.objects.bulk_update(items_to_update, ["received_quantity", "updated_at"])
        purchase.reception_notes = payload.notes
        purchase.reception_mismatch = purchase.items.exclude(quantity=F("received_quantity")).exists()
        update_fields = ["reception_notes", "reception_mismatch", "updated_at"]
        if intent == "confirm_reception":
            delivery_was_shipping = purchase.delivery_condition == PurchaseRequest.DeliveryCondition.SHIPPING
            if delivery_was_shipping:
                purchase.delivery_condition = PurchaseRequest.DeliveryCondition.IMMEDIATE
                purchase.shipping_eta = None
                update_fields.extend(["delivery_condition", "shipping_eta"])
        purchase.save(update_fields=update_fields)

    def _persist_attachments(self, purchase: PurchaseRequest, attachments: Iterable) -> None:
        for uploaded in attachments:
            if not uploaded:
                continue
            PurchaseReceptionAttachment.objects.create(
                purchase=purchase,
                file=uploaded,
                uploaded_by=self.actor if getattr(self.actor, "is_authenticated", False) else None,
            )

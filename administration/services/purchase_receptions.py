from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Sequence

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from administration.models import PurchaseItem, PurchaseReceptionAttachment, PurchaseRequest
from production.models import ChickenHouse, Farm
from inventory.services import InventoryReference, InventoryService


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
            .prefetch_related("items__product", "items__scope_farm", "items__scope_chicken_house")
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
        previous_quantities = {item.id: item.received_quantity for item in purchase.items.all()}
        inventory_service = InventoryService(
            actor=self.actor if getattr(self.actor, "is_authenticated", False) else None
        )
        for item_payload in payload.items:
            purchase_item = items_by_id.get(item_payload.item_id)
            if not purchase_item:
                continue
            previous_quantity = previous_quantities.get(purchase_item.id) or Decimal("0.00")
            purchase_item.received_quantity = item_payload.received_quantity
            purchase_item.updated_at = timezone.now()
            items_to_update.append(purchase_item)
            delta = purchase_item.received_quantity - previous_quantity
            self._register_inventory_receipt(
                purchase=purchase,
                item=purchase_item,
                delta=delta,
                inventory_service=inventory_service,
            )
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

    def _register_inventory_receipt(
        self,
        *,
        purchase: PurchaseRequest,
        item: PurchaseItem,
        delta: Decimal,
        inventory_service: InventoryService,
    ) -> None:
        if delta == 0 or not item.product_id:
            return
        scope, farm, chicken_house = self._resolve_inventory_scope(item)
        reference = InventoryReference(model_label="administration.purchaseitem", instance_id=item.pk)
        inventory_service.register_receipt(
            product=item.product,
            scope=scope,
            quantity=delta,
            farm=farm,
            chicken_house=chicken_house,
            notes=f"Ingreso por recepción de compra {purchase.timeline_code}",
            metadata={"purchase_id": purchase.pk, "purchase_item_id": item.pk},
            effective_date=purchase.purchase_date or timezone.localdate(),
            reference=reference,
        )

    def _resolve_inventory_scope(self, item: PurchaseItem) -> tuple[str, Farm | None, ChickenHouse | None]:
        area = item.scope_area or PurchaseRequest.AreaScope.COMPANY
        if area == PurchaseRequest.AreaScope.CHICKEN_HOUSE and item.scope_chicken_house:
            farm = item.scope_farm or item.scope_chicken_house.farm
            return area, farm, item.scope_chicken_house
        if area == PurchaseRequest.AreaScope.FARM and item.scope_farm:
            return area, item.scope_farm, None
        return PurchaseRequest.AreaScope.COMPANY, None, None

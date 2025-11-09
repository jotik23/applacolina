from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from administration.models import PurchaseRequest, Supplier


@dataclass
class PurchaseOrderPayload:
    purchase_id: int
    purchase_date: date
    delivery_condition: str
    shipping_eta: date | None
    shipping_notes: str
    payment_condition: str
    payment_method: str
    supplier_account_holder_id: str
    supplier_account_holder_name: str
    supplier_account_type: str
    supplier_account_number: str
    supplier_bank_name: str
    assigned_manager_id: int | None = None


class PurchaseOrderValidationError(Exception):
    def __init__(self, *, field_errors: dict[str, list[str]] | None = None) -> None:
        super().__init__("Invalid purchase order data")
        self.field_errors = field_errors or {}


class PurchaseOrderService:
    def __init__(self, *, actor) -> None:
        self.actor = actor

    def save(self, *, payload: PurchaseOrderPayload, intent: str) -> PurchaseRequest:
        if not payload.purchase_id:
            raise PurchaseOrderValidationError(field_errors={"non_field": ["Selecciona una solicitud válida."]})
        with transaction.atomic():
            purchase = self._load_purchase(payload.purchase_id)
            field_errors = self._validate(payload, purchase)
            if field_errors:
                raise PurchaseOrderValidationError(field_errors=field_errors)
            self._persist_purchase(purchase, payload, intent=intent)
            return purchase

    def _load_purchase(self, purchase_id: int) -> PurchaseRequest:
        try:
            return (
                PurchaseRequest.objects.select_for_update()
                .select_related("supplier")
                .get(pk=purchase_id)
            )
        except PurchaseRequest.DoesNotExist as exc:
            raise PurchaseOrderValidationError(field_errors={"non_field": ["La solicitud seleccionada no existe."]}) from exc

    def _validate(
        self,
        payload: PurchaseOrderPayload,
        purchase: PurchaseRequest,
    ) -> dict[str, list[str]]:
        errors: dict[str, list[str]] = {}
        if purchase.status not in {PurchaseRequest.Status.APPROVED, PurchaseRequest.Status.RECEPTION}:
            errors.setdefault("non_field", []).append("Solo puedes gestionar compras aprobadas o en gestión de pago.")
        if not payload.purchase_date:
            errors.setdefault("purchase_date", []).append("Selecciona la fecha de compra.")
        if payload.delivery_condition not in PurchaseRequest.DeliveryCondition.values:
            errors.setdefault("delivery_condition", []).append("Selecciona una condición de entrega válida.")
        elif payload.delivery_condition == PurchaseRequest.DeliveryCondition.SHIPPING and not payload.shipping_eta:
            errors.setdefault("shipping_eta", []).append("Ingresa la fecha estimada de llegada.")
        if payload.payment_condition not in PurchaseRequest.PaymentCondition.values:
            errors.setdefault("payment_condition", []).append("Selecciona una condición de pago válida.")
        if payload.payment_method not in PurchaseRequest.PaymentMethod.values:
            errors.setdefault("payment_method", []).append("Selecciona un medio de pago válido.")
        require_bank_data = payload.payment_method == PurchaseRequest.PaymentMethod.TRANSFER
        if require_bank_data:
            account_types = dict(Supplier.ACCOUNT_TYPE_CHOICES)
            if not payload.supplier_account_holder_name:
                errors.setdefault("supplier_account_holder_name", []).append("Ingresa el titular de la cuenta.")
            if not payload.supplier_account_holder_id:
                errors.setdefault("supplier_account_holder_id", []).append("Ingresa la identificación del titular.")
            if payload.supplier_account_type not in account_types:
                errors.setdefault("supplier_account_type", []).append("Selecciona un tipo de cuenta válido.")
            if not payload.supplier_account_number:
                errors.setdefault("supplier_account_number", []).append("Ingresa el número de cuenta.")
            if not payload.supplier_bank_name:
                errors.setdefault("supplier_bank_name", []).append("Ingresa el banco.")
        if payload.assigned_manager_id:
            user_model = get_user_model()
            if not user_model.objects.filter(pk=payload.assigned_manager_id).only("pk").exists():
                errors.setdefault("assigned_manager", []).append("Selecciona un gestor válido.")
        return errors

    def _persist_purchase(self, purchase: PurchaseRequest, payload: PurchaseOrderPayload, *, intent: str) -> None:
        purchase.purchase_date = payload.purchase_date
        purchase.delivery_condition = payload.delivery_condition
        purchase.shipping_eta = (
            payload.shipping_eta if payload.delivery_condition == PurchaseRequest.DeliveryCondition.SHIPPING else None
        )
        purchase.shipping_notes = (
            payload.shipping_notes if payload.delivery_condition == PurchaseRequest.DeliveryCondition.SHIPPING else ""
        )
        purchase.payment_condition = payload.payment_condition
        purchase.payment_method = payload.payment_method
        self._sync_payment_date(purchase, payload.payment_condition)
        if payload.assigned_manager_id:
            purchase.assigned_manager_id = payload.assigned_manager_id
        elif not purchase.assigned_manager_id and purchase.requester_id:
            purchase.assigned_manager_id = purchase.requester_id
        if payload.payment_method == PurchaseRequest.PaymentMethod.TRANSFER:
            purchase.supplier_account_holder_id = payload.supplier_account_holder_id
            purchase.supplier_account_holder_name = payload.supplier_account_holder_name
            purchase.supplier_account_type = payload.supplier_account_type
            purchase.supplier_account_number = payload.supplier_account_number
            purchase.supplier_bank_name = payload.supplier_bank_name
            purchase.payment_account = payload.supplier_account_number
        if intent == "confirm_order":
            purchase.status = self._determine_status_after_management(
                delivery_condition=payload.delivery_condition,
            )
        purchase.save()
        if intent == "confirm_order" and payload.delivery_condition == PurchaseRequest.DeliveryCondition.IMMEDIATE:
            self._auto_receive_items(purchase)
        if payload.payment_method == PurchaseRequest.PaymentMethod.TRANSFER:
            self._sync_supplier_bank_data(purchase.supplier, payload)

    def _sync_supplier_bank_data(self, supplier: Supplier, payload: PurchaseOrderPayload) -> None:
        if not supplier:
            return
        updated_fields: list[str] = []
        if supplier.account_holder_id != payload.supplier_account_holder_id:
            supplier.account_holder_id = payload.supplier_account_holder_id
            updated_fields.append("account_holder_id")
        if supplier.account_holder_name != payload.supplier_account_holder_name:
            supplier.account_holder_name = payload.supplier_account_holder_name
            updated_fields.append("account_holder_name")
        if supplier.account_type != payload.supplier_account_type:
            supplier.account_type = payload.supplier_account_type
            updated_fields.append("account_type")
        if supplier.account_number != payload.supplier_account_number:
            supplier.account_number = payload.supplier_account_number
            updated_fields.append("account_number")
        if supplier.bank_name != payload.supplier_bank_name:
            supplier.bank_name = payload.supplier_bank_name
            updated_fields.append("bank_name")
        if updated_fields:
            supplier.save(update_fields=updated_fields + ["updated_at"])

    def _sync_payment_date(self, purchase: PurchaseRequest, payment_condition: str) -> None:
        if payment_condition in {
            PurchaseRequest.PaymentCondition.CASH,
            PurchaseRequest.PaymentCondition.CREDIT_PAID,
        }:
            purchase.payment_date = timezone.localdate()
        else:
            purchase.payment_date = None

    def _auto_receive_items(self, purchase: PurchaseRequest) -> None:
        for item in purchase.items.all():
            item.received_quantity = item.quantity
            item.save(update_fields=["received_quantity", "updated_at"])

    def _determine_status_after_management(self, *, delivery_condition: str) -> str:
        return PurchaseRequest.Status.RECEPTION

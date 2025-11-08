from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from administration.models import PurchaseRequest, Supplier


@dataclass
class PurchasePaymentPayload:
    purchase_id: int
    payment_method: str
    payment_condition: str
    payment_source: str
    payment_notes: str
    supplier_account_holder_id: str
    supplier_account_holder_name: str
    supplier_account_type: str
    supplier_account_number: str
    supplier_bank_name: str


class PurchasePaymentValidationError(Exception):
    def __init__(self, *, field_errors: dict[str, list[str]] | None = None) -> None:
        super().__init__("Invalid payment payload")
        self.field_errors = field_errors or {}


class PurchasePaymentService:
    def __init__(self, *, actor) -> None:
        self.actor = actor

    def save(self, *, payload: PurchasePaymentPayload, intent: str) -> PurchaseRequest:
        if not payload.purchase_id:
            raise PurchasePaymentValidationError(field_errors={"non_field": ["Selecciona una solicitud válida."]})
        with transaction.atomic():
            purchase = self._load_purchase(payload.purchase_id)
            field_errors = self._validate(payload, purchase, intent=intent)
            if field_errors:
                raise PurchasePaymentValidationError(field_errors=field_errors)
            self._persist_payment(purchase, payload, intent=intent)
            return purchase

    def _load_purchase(self, purchase_id: int) -> PurchaseRequest:
        try:
            return (
                PurchaseRequest.objects.select_for_update()
                .select_related("supplier")
                .get(pk=purchase_id)
            )
        except PurchaseRequest.DoesNotExist as exc:
            raise PurchasePaymentValidationError(field_errors={"non_field": ["La solicitud seleccionada no existe."]}) from exc

    def _validate(
        self,
        payload: PurchasePaymentPayload,
        purchase: PurchaseRequest,
        *,
        intent: str,
    ) -> dict[str, list[str]]:
        errors: dict[str, list[str]] = {}
        allowed_statuses = {
            PurchaseRequest.Status.RECEPTION,
            PurchaseRequest.Status.INVOICE,
        }
        if purchase.status not in allowed_statuses:
            errors.setdefault("non_field", []).append("Solo puedes registrar pagos para compras en Por pagar.")
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
        return errors

    def _persist_payment(self, purchase: PurchaseRequest, payload: PurchasePaymentPayload, *, intent: str) -> None:
        purchase.payment_method = payload.payment_method
        purchase.payment_notes = payload.payment_notes
        purchase.payment_source = payload.payment_source
        payment_condition = payload.payment_condition
        if intent == "confirm_payment" and payment_condition == PurchaseRequest.PaymentCondition.CREDIT:
            payment_condition = PurchaseRequest.PaymentCondition.CREDIT_PAID
        purchase.payment_condition = payment_condition
        if payload.payment_method == PurchaseRequest.PaymentMethod.TRANSFER:
            purchase.supplier_account_holder_id = payload.supplier_account_holder_id
            purchase.supplier_account_holder_name = payload.supplier_account_holder_name
            purchase.supplier_account_type = payload.supplier_account_type
            purchase.supplier_account_number = payload.supplier_account_number
            purchase.supplier_bank_name = payload.supplier_bank_name
            purchase.payment_account = payload.supplier_account_number or purchase.payment_account
        self._sync_payment_date(purchase, payment_condition)
        update_fields = [
            "payment_method",
            "payment_condition",
            "payment_notes",
            "payment_source",
            "updated_at",
        ]
        if payment_condition in {
            PurchaseRequest.PaymentCondition.CASH,
            PurchaseRequest.PaymentCondition.CREDIT_PAID,
        } or purchase.payment_date:
            update_fields.append("payment_date")
        if payload.payment_method == PurchaseRequest.PaymentMethod.TRANSFER:
            update_fields.extend(
                [
                    "supplier_account_holder_id",
                    "supplier_account_holder_name",
                    "supplier_account_type",
                    "supplier_account_number",
                    "supplier_bank_name",
                    "payment_account",
                ]
            )
        if intent == "confirm_payment":
            purchase.status = PurchaseRequest.Status.INVOICE
            update_fields.append("status")
        purchase.save(update_fields=update_fields)
        if payload.payment_method == PurchaseRequest.PaymentMethod.TRANSFER:
            self._sync_supplier_bank_data(purchase.supplier, payload)

    def _sync_supplier_bank_data(self, supplier: Supplier, payload: PurchasePaymentPayload) -> None:
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

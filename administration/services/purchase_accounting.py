from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from administration.models import PurchaseRequest


@dataclass
class PurchaseAccountingPayload:
    purchase_id: int


class PurchaseAccountingValidationError(Exception):
    def __init__(self, *, field_errors: dict[str, list[str]] | None = None) -> None:
        super().__init__("Invalid accounting payload")
        self.field_errors = field_errors or {}


class PurchaseAccountingService:
    def __init__(self, *, actor) -> None:
        self.actor = actor

    def mark_accounted(self, *, payload: PurchaseAccountingPayload) -> PurchaseRequest:
        if not payload.purchase_id:
            raise PurchaseAccountingValidationError(
                field_errors={'non_field': ["Selecciona una solicitud válida."]}
            )
        with transaction.atomic():
            purchase = self._load_purchase(payload.purchase_id)
            field_errors = self._validate(purchase)
            if field_errors:
                raise PurchaseAccountingValidationError(field_errors=field_errors)
            self._persist(purchase)
            return purchase

    def _load_purchase(self, purchase_id: int) -> PurchaseRequest:
        try:
            return PurchaseRequest.objects.select_for_update().get(pk=purchase_id)
        except PurchaseRequest.DoesNotExist as exc:
            raise PurchaseAccountingValidationError(
                field_errors={'non_field': ["La solicitud seleccionada ya no existe."]}
            ) from exc

    def _validate(self, purchase: PurchaseRequest) -> dict[str, list[str]]:
        errors: dict[str, list[str]] = {}
        if purchase.status != PurchaseRequest.Status.PAYMENT:
            errors.setdefault('non_field', []).append(
                "Solo puedes contabilizar compras en Contabilidad."
            )
        if purchase.accounted_in_system:
            errors.setdefault('non_field', []).append("Esta compra ya fue contabilizada.")
        return errors

    def _persist(self, purchase: PurchaseRequest) -> None:
        purchase.accounted_in_system = True
        purchase.accounted_at = timezone.now()
        purchase.status = PurchaseRequest.Status.ARCHIVED
        purchase.save(
            update_fields=[
                'accounted_in_system',
                'accounted_at',
                'status',
                'updated_at',
            ]
        )

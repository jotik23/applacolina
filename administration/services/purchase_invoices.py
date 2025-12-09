from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from django.db import transaction
from django.utils import timezone

from administration.models import PurchaseRequest, PurchaseSupportAttachment, SupportDocumentType


@dataclass
class PurchaseInvoicePayload:
    purchase_id: int
    support_document_type_id: int | None
    template_values: dict[str, str]


class PurchaseInvoiceValidationError(Exception):
    def __init__(self, *, field_errors: dict[str, list[str]] | None = None) -> None:
        super().__init__("Invalid invoice payload")
        self.field_errors = field_errors or {}


class PurchaseInvoiceService:
    def __init__(self, *, actor) -> None:
        self.actor = actor

    def save(
        self,
        *,
        payload: PurchaseInvoicePayload,
        intent: str,
        attachments: Iterable,
    ) -> PurchaseRequest:
        if not payload.purchase_id:
            raise PurchaseInvoiceValidationError(field_errors={"non_field": ["Selecciona una solicitud válida."]})
        with transaction.atomic():
            purchase = self._load_purchase(payload.purchase_id)
            support_type = self._load_support_type(payload.support_document_type_id)
            field_errors = self._validate(purchase=purchase, support_type=support_type)
            if field_errors:
                raise PurchaseInvoiceValidationError(field_errors=field_errors)
            self._persist_invoice(
                purchase=purchase,
                support_type=support_type,
                template_values=payload.template_values,
                intent=intent,
            )
            self._persist_attachments(purchase=purchase, attachments=attachments)
            return purchase

    def _load_purchase(self, purchase_id: int) -> PurchaseRequest:
        try:
            return PurchaseRequest.objects.select_for_update().get(pk=purchase_id)
        except PurchaseRequest.DoesNotExist as exc:
            raise PurchaseInvoiceValidationError(
                field_errors={"non_field": ["La solicitud seleccionada ya no existe."]}
            ) from exc

    def _load_support_type(self, support_type_id: int | None) -> SupportDocumentType:
        if not support_type_id:
            raise PurchaseInvoiceValidationError(
                field_errors={"support_document_type": ["Selecciona el tipo de soporte a registrar."]}
            )
        try:
            return SupportDocumentType.objects.get(pk=support_type_id)
        except SupportDocumentType.DoesNotExist as exc:
            raise PurchaseInvoiceValidationError(
                field_errors={"support_document_type": ["El tipo de soporte seleccionado no existe."]}
            ) from exc

    def _validate(
        self,
        *,
        purchase: PurchaseRequest,
        support_type: SupportDocumentType,
    ) -> dict[str, list[str]]:
        errors: dict[str, list[str]] = {}
        allowed_statuses = {
            PurchaseRequest.Status.INVOICE,
        }
        if purchase.status not in allowed_statuses:
            errors.setdefault("non_field", []).append(
                "Solo puedes gestionar soportes para compras en Gestionar soporte."
            )
        if purchase.support_group_code and purchase.support_group_leader_id:
            leader = purchase.support_group_anchor()
            leader_label = leader.timeline_code if leader else "otra solicitud"
            errors.setdefault("non_field", []).append(
                f"Esta compra hace parte del grupo {purchase.support_group_code}. Gestiona el soporte desde {leader_label}."
            )
        if support_type.kind == SupportDocumentType.Kind.INTERNAL and not support_type.template.strip():
            errors.setdefault("non_field", []).append(
                "El soporte interno seleccionado no tiene plantilla configurada."
            )
        return errors

    def _persist_invoice(
        self,
        *,
        purchase: PurchaseRequest,
        support_type: SupportDocumentType,
        template_values: dict[str, str],
        intent: str,
    ) -> None:
        purchase.support_document_type = support_type
        purchase.support_template_values = self._clean_template_values(template_values)
        update_fields = ["support_document_type", "support_template_values", "updated_at"]
        if intent == "confirm_invoice":
            purchase.status = PurchaseRequest.Status.PAYMENT
            update_fields.append("status")
        purchase.save(update_fields=update_fields)
        self._sync_support_group(purchase=purchase, move_to_payment=intent == "confirm_invoice")

    def _persist_attachments(self, *, purchase: PurchaseRequest, attachments: Iterable) -> None:
        for uploaded in attachments:
            if not uploaded:
                continue
            PurchaseSupportAttachment.objects.create(
                purchase=purchase,
                file=uploaded,
                uploaded_by=self.actor if getattr(self.actor, "is_authenticated", False) else None,
            )

    def _clean_template_values(self, values: dict[str, str]) -> dict[str, str]:
        cleaned: dict[str, str] = {}
        for key, value in values.items():
            normalized_key = (key or "").strip()
            if not normalized_key:
                continue
            cleaned[normalized_key] = (value or "").strip()
        return cleaned

    def _sync_support_group(self, *, purchase: PurchaseRequest, move_to_payment: bool) -> None:
        if not purchase.support_group_code or not purchase.is_support_group_leader:
            return
        followers = PurchaseRequest.objects.filter(
            support_group_code=purchase.support_group_code
        ).exclude(pk=purchase.pk)
        if not followers:
            return
        now = timezone.now()
        update_kwargs: dict[str, object] = {
            'support_document_type': purchase.support_document_type,
            'support_template_values': purchase.support_template_values,
            'updated_at': now,
        }
        if move_to_payment:
            update_kwargs['status'] = PurchaseRequest.Status.PAYMENT
        followers.update(**update_kwargs)

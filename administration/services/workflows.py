from __future__ import annotations

from typing import Iterable, Sequence

from django.db import transaction
from django.utils import timezone

from administration.models import (
    ExpenseTypeApprovalRule,
    PurchaseApproval,
    PurchaseAuditLog,
    PurchaseRequest,
)


class PurchaseApprovalWorkflowService:
    """Generate approvals for a purchase request based on its expense type workflow."""

    AUTO_APPROVAL_MESSAGE = "Paso aprobado automáticamente para el solicitante."

    def __init__(self, *, purchase_request: PurchaseRequest, actor):
        self.purchase_request = purchase_request
        self.actor = actor

    def run(self) -> None:
        with transaction.atomic():
            PurchaseApproval.objects.filter(purchase_request=self.purchase_request).delete()
            self.purchase_request.approved_at = None
            self.purchase_request.save(update_fields=["approved_at"])

            rules = list(
                ExpenseTypeApprovalRule.objects.filter(expense_type=self.purchase_request.expense_type)
                .select_related("approver")
                .order_by("id")
            )

            if not rules:
                self._auto_approve_without_rules()
                return

            approvals = self._create_approvals(rules)
            auto_ids = self._auto_approve_requester_steps(approvals)
            self._finalize_state(auto_ids)

    def _create_approvals(self, rules: Sequence[ExpenseTypeApprovalRule]) -> list[PurchaseApproval]:
        approvals: list[PurchaseApproval] = []
        for index, rule in enumerate(rules, start=1):
            approvals.append(
                PurchaseApproval.objects.create(
                    purchase_request=self.purchase_request,
                    rule=rule,
                    sequence=index,
                    role=self._role_label(index=index, rule=rule),
                    approver=rule.approver,
                )
            )
        return approvals

    def _auto_approve_without_rules(self) -> None:
        timestamp = timezone.now()
        PurchaseApproval.objects.create(
            purchase_request=self.purchase_request,
            sequence=1,
            role="Aprobación automática",
            approver=self.purchase_request.requester,
            status=PurchaseApproval.Status.APPROVED,
            comments="Solicitud aprobada automáticamente sin flujo configurado.",
            decided_at=timestamp,
        )
        self.purchase_request.status = PurchaseRequest.Status.APPROVED
        self.purchase_request.approved_at = timestamp
        self.purchase_request.save(update_fields=["status", "approved_at"])
        self._log(
            event="request-auto-approved",
            message="Solicitud aprobada automáticamente por falta de flujo configurado.",
        )

    def _auto_approve_requester_steps(self, approvals: Iterable[PurchaseApproval]) -> list[int]:
        auto_ids: list[int] = []
        requester_id = self.purchase_request.requester_id
        if not requester_id:
            return auto_ids

        timestamp = timezone.now()
        for approval in approvals:
            if approval.approver_id != requester_id:
                continue
            approval.status = PurchaseApproval.Status.APPROVED
            approval.comments = self.AUTO_APPROVAL_MESSAGE
            approval.decided_at = timestamp
            approval.save(update_fields=["status", "comments", "decided_at", "updated_at"])
            auto_ids.append(approval.id)

        if auto_ids:
            self._log(
                event="approval-steps-auto-approved",
                message="Se aprobaron automáticamente pasos del solicitante.",
                payload={"approval_ids": auto_ids},
            )
        return auto_ids

    def _finalize_state(self, auto_ids: list[int]) -> None:
        pending_exists = PurchaseApproval.objects.filter(
            purchase_request=self.purchase_request,
            status=PurchaseApproval.Status.PENDING,
        ).exists()
        if pending_exists:
            self.purchase_request.status = PurchaseRequest.Status.SUBMITTED
            self.purchase_request.approved_at = None
            self.purchase_request.save(update_fields=["status", "approved_at"])
            self._log(
                event="request-submitted",
                message="Solicitud enviada a aprobación.",
                payload={"auto_approved_ids": auto_ids},
            )
            return

        timestamp = timezone.now()
        self.purchase_request.status = PurchaseRequest.Status.APPROVED
        self.purchase_request.approved_at = timestamp
        self.purchase_request.save(update_fields=["status", "approved_at"])
        self._log(
            event="request-approved",
            message="Solicitud aprobada completamente.",
            payload={"auto_approved_ids": auto_ids},
        )

    def _log(self, *, event: str, message: str, payload: dict | None = None) -> None:
        PurchaseAuditLog.objects.create(
            purchase_request=self.purchase_request,
            event=event,
            message=message,
            payload=payload or {},
            actor=self.actor if getattr(self.actor, "is_authenticated", False) else None,
        )

    def _role_label(self, *, index: int, rule: ExpenseTypeApprovalRule) -> str:
        approver = rule.approver
        label = ''
        if approver:
            get_full_name = getattr(approver, "get_full_name", None)
            if callable(get_full_name):
                label = (get_full_name() or '').strip()
            if not label:
                label = getattr(approver, "email", "") or str(approver)
        return label or f"Aprobador {index}"

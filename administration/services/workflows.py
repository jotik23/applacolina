from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Iterable, Sequence

from django.db import transaction
from django.utils import timezone

from administration.models import (
    ExpenseTypeApprovalRule,
    PurchaseApproval,
    PurchaseAuditLog,
    PurchaseRequest,
    PurchasingExpenseType,
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


class ExpenseTypeWorkflowRefreshService:
    """Rebuild purchase approvals when a category workflow changes."""

    ELIGIBLE_STATUSES: ClassVar[tuple[str, ...]] = (
        PurchaseRequest.Status.SUBMITTED,
        PurchaseRequest.Status.APPROVED,
    )

    def __init__(self, *, expense_type: PurchasingExpenseType, actor):
        self.expense_type = expense_type
        self.actor = actor

    def run(self, *, chunk_size: int = 50) -> int:
        purchases = (
            PurchaseRequest.objects.filter(
                expense_type=self.expense_type,
                status__in=self.ELIGIBLE_STATUSES,
            )
            .select_related("expense_type", "requester")
            .order_by("pk")
        )
        refreshed = 0
        for purchase in purchases.iterator(chunk_size=chunk_size):
            PurchaseApprovalWorkflowService(
                purchase_request=purchase,
                actor=self.actor,
            ).run()
            refreshed += 1
        return refreshed


class PurchaseApprovalDecisionError(Exception):
    """Raised when an approval decision cannot be applied."""


@dataclass(frozen=True)
class ApprovalDecisionResult:
    approval: PurchaseApproval
    purchase_status: str
    workflow_completed: bool
    decision: str


class PurchaseApprovalDecisionService:
    """Registers manual approval or rejection decisions for a purchase request."""

    def __init__(self, *, purchase_request: PurchaseRequest, actor):
        self.purchase_request = purchase_request
        self.actor = actor

    def approve(self, *, note: str | None = None) -> ApprovalDecisionResult:
        return self._decide(
            target_status=PurchaseApproval.Status.APPROVED,
            note=note or "",
        )

    def reject(self, *, note: str | None = None) -> ApprovalDecisionResult:
        return self._decide(
            target_status=PurchaseApproval.Status.REJECTED,
            note=note or "",
        )

    def _decide(self, *, target_status: str, note: str) -> ApprovalDecisionResult:
        note = note.strip()
        if not getattr(self.actor, "is_authenticated", False):
            raise PurchaseApprovalDecisionError("No tienes permisos para registrar aprobaciones.")
        if self.purchase_request.status != PurchaseRequest.Status.SUBMITTED:
            raise PurchaseApprovalDecisionError("La solicitud ya no está en aprobación.")

        with transaction.atomic():
            approval = (
                PurchaseApproval.objects.select_for_update()
                .filter(
                    purchase_request=self.purchase_request,
                    approver=self.actor,
                    status=PurchaseApproval.Status.PENDING,
                )
                .order_by("sequence")
                .first()
            )
            if not approval:
                raise PurchaseApprovalDecisionError("No tienes aprobaciones pendientes para esta solicitud.")

            timestamp = timezone.now()
            approval.status = target_status
            approval.comments = note
            approval.decided_at = timestamp
            approval.save(update_fields=["status", "comments", "decided_at", "updated_at"])

            if target_status == PurchaseApproval.Status.REJECTED:
                self._handle_rejection(approval=approval, note=note)
                return ApprovalDecisionResult(
                    approval=approval,
                    purchase_status=self.purchase_request.status,
                    workflow_completed=False,
                    decision="rejected",
                )

            completed = self._handle_approval(approval=approval)
            return ApprovalDecisionResult(
                approval=approval,
                purchase_status=self.purchase_request.status,
                workflow_completed=completed,
                decision="approved",
            )

    def _handle_rejection(self, *, approval: PurchaseApproval, note: str) -> None:
        self.purchase_request.status = PurchaseRequest.Status.DRAFT
        self.purchase_request.approved_at = None
        self.purchase_request.save(update_fields=["status", "approved_at", "updated_at"])
        self._log(
            event="approval-rejected",
            message="Solicitud rechazada por un aprobador.",
            payload={
                "approval_id": approval.id,
                "note": note,
            },
        )

    def _handle_approval(self, *, approval: PurchaseApproval) -> bool:
        pending_qs = PurchaseApproval.objects.filter(
            purchase_request=self.purchase_request,
            status=PurchaseApproval.Status.PENDING,
        )
        pending_count = pending_qs.count()
        if pending_count:
            self.purchase_request.status = PurchaseRequest.Status.SUBMITTED
            self.purchase_request.approved_at = None
            self.purchase_request.save(update_fields=["status", "approved_at", "updated_at"])
            self._log(
                event="approval-step-approved",
                message="Aprobación registrada. El flujo continúa con el siguiente aprobador.",
                payload={
                    "approval_id": approval.id,
                    "pending_count": pending_count,
                },
            )
            return False

        timestamp = timezone.now()
        self.purchase_request.status = PurchaseRequest.Status.APPROVED
        self.purchase_request.approved_at = timestamp
        self.purchase_request.save(update_fields=["status", "approved_at", "updated_at"])
        self._log(
            event="request-approved",
            message="Solicitud aprobada completamente por los aprobadores.",
            payload={"approval_id": approval.id},
        )
        return True

    def _log(self, *, event: str, message: str, payload: dict | None = None) -> None:
        PurchaseAuditLog.objects.create(
            purchase_request=self.purchase_request,
            event=event,
            message=message,
            payload=payload or {},
            actor=self.actor if getattr(self.actor, "is_authenticated", False) else None,
        )

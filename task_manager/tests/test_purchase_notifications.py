from __future__ import annotations

from django.test import TestCase

from administration.models import PurchaseRequest, PurchasingExpenseType, Supplier
from personal.models import UserProfile
from task_manager.services.purchase_notifications import (
    notify_purchase_manager_assignment,
    notify_purchase_returned_for_changes,
    notify_purchase_workflow_result,
)
from task_manager.services.push_notifications import PushNotificationMessage, PushNotificationResult


class _StubPushService:
    def __init__(self):
        self.sent: list[dict[str, object]] = []

    def send_to_user(
        self,
        *,
        user: UserProfile,
        message: PushNotificationMessage,
        notification_type: str,
        ttl: int = 300,
    ) -> PushNotificationResult:
        self.sent.append(
            {
                "user": user,
                "message": message,
                "notification_type": notification_type,
                "ttl": ttl,
            }
        )
        return PushNotificationResult(attempted=1, delivered=1, failures=[])


class PurchaseNotificationTests(TestCase):
    def setUp(self):
        self.requester = UserProfile.objects.create_user(
            cedula="123",
            password="pwd",
            nombres="Ana",
            apellidos="López",
            telefono="3001230000",
        )
        self.manager = UserProfile.objects.create_user(
            cedula="456",
            password="pwd",
            nombres="Carlos",
            apellidos="Díaz",
            telefono="3001230001",
        )
        self.supplier = Supplier.objects.create(name="Proveedor", tax_id="900123")
        self.expense_type = PurchasingExpenseType.objects.create(name="Insumos")
        self.purchase = PurchaseRequest.objects.create(
            timeline_code="PO-1",
            name="Compra de alimento",
            supplier=self.supplier,
            expense_type=self.expense_type,
            requester=self.requester,
            status=PurchaseRequest.Status.SUBMITTED,
        )

    def test_notify_purchase_workflow_result_targets_requester(self):
        service = _StubPushService()

        notify_purchase_workflow_result(
            purchase=self.purchase,
            decision="approved",
            workflow_completed=True,
            approver=self.manager,
            service=service,
        )

        self.assertEqual(len(service.sent), 1)
        payload = service.sent[0]
        self.assertEqual(payload["user"], self.requester)
        message = payload["message"]
        self.assertIsInstance(message, PushNotificationMessage)
        self.assertIn("aprobada", message.title.lower())
        self.assertEqual(message.data["purchase_id"], self.purchase.pk)
        self.assertEqual(payload["notification_type"], "purchase.workflow-result")

    def test_notify_purchase_manager_assignment_targets_manager(self):
        service = _StubPushService()

        notify_purchase_manager_assignment(
            purchase=self.purchase,
            manager=self.manager,
            service=service,
            source="test",
        )

        self.assertEqual(len(service.sent), 1)
        payload = service.sent[0]
        self.assertEqual(payload["user"], self.manager)
        self.assertEqual(payload["notification_type"], "purchase.manager-assigned")
        self.assertEqual(payload["message"].action.label, "Gestionar compra")

    def test_notify_purchase_returned_for_changes_skips_without_requester(self):
        service = _StubPushService()
        self.purchase.requester = None
        self.purchase.save(update_fields=["requester"])

        notify_purchase_returned_for_changes(
            purchase=self.purchase,
            manager=self.manager,
            reason="Falta la cotización",
            service=service,
        )

        self.assertEqual(service.sent, [])

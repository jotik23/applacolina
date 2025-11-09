from __future__ import annotations

from decimal import Decimal

from django.test import TestCase

from administration.models import PurchaseRequest, PurchasingExpenseType, Supplier
from personal.models import UserProfile
from task_manager.mini_app.features import build_purchase_requests_overview


class MiniAppPurchaseOverviewTests(TestCase):
    def setUp(self):
        self._purchase_sequence = 0
        self.requester = self._create_user(identifier="1001", first_name="Laura")
        self.manager = self._create_user(identifier="1002", first_name="Mateo")
        self.other_user = self._create_user(identifier="1003", first_name="Daniela")
        self.expense_type = PurchasingExpenseType.objects.create(name="Bioseguridad")
        self.supplier = Supplier.objects.create(name="Proveedor Uno", tax_id="900123456")

    def _create_user(self, *, identifier: str, first_name: str) -> UserProfile:
        return UserProfile.objects.create_user(
            cedula=f"{identifier}000",
            password=None,
            nombres=first_name,
            apellidos="Test",
            telefono=f"301{identifier}",
        )

    def _next_timeline_code(self) -> str:
        self._purchase_sequence += 1
        return f"SOL-TEST-{self._purchase_sequence:04d}"

    def _create_purchase(
        self,
        *,
        requester: UserProfile | None,
        assigned_manager: UserProfile | None,
        status: str,
        estimated_total: Decimal,
    ) -> PurchaseRequest:
        return PurchaseRequest.objects.create(
            timeline_code=self._next_timeline_code(),
            name=f"Compra {self._purchase_sequence}",
            supplier=self.supplier,
            expense_type=self.expense_type,
            requester=requester,
            assigned_manager=assigned_manager,
            status=status,
            estimated_total=estimated_total,
        )

    def test_overview_includes_purchases_created_or_managed_by_user(self):
        requester_purchase = self._create_purchase(
            requester=self.requester,
            assigned_manager=self.other_user,
            status=PurchaseRequest.Status.DRAFT,
            estimated_total=Decimal("125000.00"),
        )
        manager_purchase = self._create_purchase(
            requester=self.other_user,
            assigned_manager=self.requester,
            status=PurchaseRequest.Status.RECEPTION,
            estimated_total=Decimal("98000.00"),
        )
        self._create_purchase(
            requester=self.other_user,
            assigned_manager=self.other_user,
            status=PurchaseRequest.Status.SUBMITTED,
            estimated_total=Decimal("54000.00"),
        )

        overview = build_purchase_requests_overview(user=self.requester)
        self.assertIsNotNone(overview)
        assert overview is not None

        entry_ids = {entry.pk for entry in overview.entries}
        self.assertSetEqual(entry_ids, {requester_purchase.pk, manager_purchase.pk})
        self.assertEqual(overview.total_count, 2)

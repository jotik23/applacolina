from __future__ import annotations

from decimal import Decimal

from django.test import TestCase

from administration.models import PurchaseItem, PurchaseRequest, PurchasingExpenseType, Supplier
from personal.models import UserProfile
from production.models import Farm
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

    def test_draft_entry_includes_edit_payload(self):
        farm = Farm.objects.create(name="Granja Central")
        purchase = self._create_purchase(
            requester=self.requester,
            assigned_manager=self.manager,
            status=PurchaseRequest.Status.DRAFT,
            estimated_total=Decimal("0.00"),
        )
        purchase.description = "Prioridad mensual"
        purchase.shipping_notes = "Nota 1\n\nNota 2"
        purchase.save(update_fields=["description", "shipping_notes"])
        PurchaseItem.objects.create(
            purchase=purchase,
            description="Malla",
            quantity=Decimal("2.0"),
            estimated_amount=Decimal("15000.00"),
            scope_area=PurchaseRequest.AreaScope.FARM,
            scope_farm=farm,
        )

        overview = build_purchase_requests_overview(user=self.requester)
        self.assertIsNotNone(overview)
        entry = next(entry for entry in overview.entries if entry.pk == purchase.pk)
        self.assertTrue(entry.can_edit)
        self.assertIsInstance(entry.edit_payload, dict)
        payload = entry.edit_payload or {}
        self.assertEqual(payload.get("purchase_id"), purchase.pk)
        self.assertEqual(payload.get("assigned_manager_id"), self.manager.pk)
        self.assertEqual(payload.get("assigned_manager_label"), self.manager.get_full_name())
        items = payload.get("items") or []
        self.assertGreater(len(items), 0)
        self.assertEqual(items[0].get("description"), "Malla")
        self.assertEqual(items[0].get("scope_value"), f"{PurchaseRequest.AreaScope.FARM}:{farm.pk}")
        self.assertEqual(payload.get("revision_notes"), ["Nota 1", "Nota 2"])

    def test_non_draft_entry_has_no_edit_payload(self):
        purchase = self._create_purchase(
            requester=self.requester,
            assigned_manager=self.other_user,
            status=PurchaseRequest.Status.SUBMITTED,
            estimated_total=Decimal("45000.00"),
        )

        overview = build_purchase_requests_overview(user=self.requester)
        self.assertIsNotNone(overview)
        entry = next(entry for entry in overview.entries if entry.pk == purchase.pk)
        self.assertFalse(entry.can_edit)
        self.assertIsNone(entry.edit_payload)

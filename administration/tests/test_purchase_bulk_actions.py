from __future__ import annotations

from datetime import datetime, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from administration.models import PurchaseRequest, PurchasingExpenseType, Supplier
from administration.services.purchase_bulk_actions import (
    PurchaseBulkActionError,
    move_purchases_to_status,
    update_purchases_requested_date,
)


class PurchaseBulkActionsTests(TestCase):
    def setUp(self) -> None:
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            cedula='900100',
            password='test123',
            nombres='Bulk',
            apellidos='Tester',
            telefono='3000000000',
            is_staff=True,
        )
        self.supplier = Supplier.objects.create(name='Proveedor Demo', tax_id='C123')
        self.category = PurchasingExpenseType.objects.create(name='Infraestructura')

    def test_move_purchases_to_status_updates_records(self) -> None:
        draft = self._create_purchase(status=PurchaseRequest.Status.DRAFT)
        submitted = self._create_purchase(status=PurchaseRequest.Status.SUBMITTED)

        updated = move_purchases_to_status(
            purchase_ids=[draft.pk, submitted.pk],
            target_status=PurchaseRequest.Status.APPROVED,
        )

        self.assertEqual(2, updated)
        draft.refresh_from_db()
        submitted.refresh_from_db()
        self.assertEqual(PurchaseRequest.Status.APPROVED, draft.status)
        self.assertEqual(PurchaseRequest.Status.APPROVED, submitted.status)

    def test_move_purchases_to_status_validates_target(self) -> None:
        purchase = self._create_purchase(status=PurchaseRequest.Status.DRAFT)

        with self.assertRaises(PurchaseBulkActionError):
            move_purchases_to_status(purchase_ids=[purchase.pk], target_status='invalid')

    def test_update_requested_date_preserves_time_component(self) -> None:
        with timezone.override('America/Bogota'):
            original = timezone.make_aware(datetime(2024, 1, 10, 14, 30))
            purchase = self._create_purchase(status=PurchaseRequest.Status.DRAFT, created_at=original)
            target_date = (original - timedelta(days=5)).date()

            updated = update_purchases_requested_date(
                purchase_ids=[purchase.pk],
                requested_date=target_date,
            )

            self.assertEqual(1, updated)
            purchase.refresh_from_db()
            local_dt = timezone.localtime(purchase.created_at)
            self.assertEqual(target_date, local_dt.date())
            self.assertEqual(original.hour, local_dt.hour)
            self.assertEqual(original.minute, local_dt.minute)

    def test_update_requested_date_validates_input(self) -> None:
        purchase = self._create_purchase(status=PurchaseRequest.Status.DRAFT)

        with self.assertRaises(PurchaseBulkActionError):
            update_purchases_requested_date(purchase_ids=[purchase.pk], requested_date=None)

    def _create_purchase(
        self,
        *,
        status: str,
        created_at: datetime | None = None,
    ) -> PurchaseRequest:
        sequence = PurchaseRequest.objects.count() + 1
        purchase = PurchaseRequest.objects.create(
            timeline_code=f'CP-{sequence:05d}',
            name='Compra masiva',
            description='Bulk',
            requester=self.user,
            supplier=self.supplier,
            expense_type=self.category,
            status=status,
            currency='COP',
            estimated_total=1,
        )
        if created_at:
            PurchaseRequest.objects.filter(pk=purchase.pk).update(created_at=created_at)
            purchase.refresh_from_db(fields=['created_at'])
        return purchase

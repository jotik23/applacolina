from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from administration.models import (
    PurchaseRequest,
    PurchaseSupportAttachment,
    PurchasingExpenseType,
    Supplier,
    SupportDocumentType,
)
from administration.services.purchase_bulk_actions import (
    PurchaseBulkActionError,
    group_purchases_for_support,
    move_purchases_to_status,
    ungroup_support_group,
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
        self.support_type = SupportDocumentType.objects.create(name='Factura de proveedor')

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

    def test_move_purchases_to_status_sets_payment_amount_after_payment(self) -> None:
        purchase = self._create_purchase(
            status=PurchaseRequest.Status.SUBMITTED,
            invoice_total=Decimal('125000.50'),
            payment_amount=Decimal('0.00'),
        )

        move_purchases_to_status(
            purchase_ids=[purchase.pk],
            target_status=PurchaseRequest.Status.PAYMENT,
        )

        purchase.refresh_from_db()
        self.assertEqual(Decimal('125000.50'), purchase.payment_amount)
        self.assertEqual(PurchaseRequest.Status.PAYMENT, purchase.status)

    def test_move_purchases_to_status_sets_payment_amount_on_support_stage(self) -> None:
        purchase = self._create_purchase(
            status=PurchaseRequest.Status.SUBMITTED,
            invoice_total=Decimal('100'),
            payment_amount=Decimal('0'),
        )

        move_purchases_to_status(
            purchase_ids=[purchase.pk],
            target_status=PurchaseRequest.Status.INVOICE,
        )

        purchase.refresh_from_db()
        self.assertEqual(Decimal('100'), purchase.payment_amount)
        self.assertEqual(PurchaseRequest.Status.INVOICE, purchase.status)

    def test_move_purchases_to_status_sets_payment_amount_on_support_stage(self) -> None:
        purchase = self._create_purchase(
            status=PurchaseRequest.Status.SUBMITTED,
            invoice_total=Decimal('100'),
            payment_amount=Decimal('0'),
        )

        move_purchases_to_status(
            purchase_ids=[purchase.pk],
            target_status=PurchaseRequest.Status.INVOICE,
        )

        purchase.refresh_from_db()
        self.assertEqual(Decimal('100'), purchase.payment_amount)
        self.assertEqual(PurchaseRequest.Status.INVOICE, purchase.status)

    def test_move_purchases_to_status_uses_estimated_total_when_invoice_missing(self) -> None:
        purchase = self._create_purchase(
            status=PurchaseRequest.Status.APPROVED,
            invoice_total=None,
            estimated_total=Decimal('212000'),
            payment_amount=Decimal('0'),
        )

        move_purchases_to_status(
            purchase_ids=[purchase.pk],
            target_status=PurchaseRequest.Status.PAYMENT,
        )

        purchase.refresh_from_db()
        self.assertEqual(Decimal('212000'), purchase.payment_amount)
        self.assertEqual(PurchaseRequest.Status.PAYMENT, purchase.status)

    def test_move_purchases_to_status_preserves_existing_payment_amount(self) -> None:
        purchase = self._create_purchase(
            status=PurchaseRequest.Status.APPROVED,
            invoice_total=Decimal('90000'),
            payment_amount=Decimal('45000'),
        )

        move_purchases_to_status(
            purchase_ids=[purchase.pk],
            target_status=PurchaseRequest.Status.PAYMENT,
        )

        purchase.refresh_from_db()
        self.assertEqual(Decimal('45000'), purchase.payment_amount)
        self.assertEqual(PurchaseRequest.Status.PAYMENT, purchase.status)

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

    def test_group_support_creates_shared_code(self) -> None:
        first = self._create_purchase(status=PurchaseRequest.Status.INVOICE)
        second = self._create_purchase(status=PurchaseRequest.Status.INVOICE)

        code, leader_id, _, count = group_purchases_for_support(purchase_ids=[first.pk, second.pk])

        self.assertEqual(2, count)
        self.assertTrue(code.startswith('SG-'))
        self.assertEqual(first.pk, leader_id)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(code, first.support_group_code)
        self.assertEqual(code, second.support_group_code)
        self.assertIsNone(first.support_group_leader)
        self.assertEqual(first, second.support_group_leader)

    def test_group_support_validates_status(self) -> None:
        draft = self._create_purchase(status=PurchaseRequest.Status.DRAFT)
        invoice = self._create_purchase(status=PurchaseRequest.Status.INVOICE)

        with self.assertRaises(PurchaseBulkActionError):
            group_purchases_for_support(purchase_ids=[draft.pk, invoice.pk])

    def test_group_support_rejects_purchases_with_attachments(self) -> None:
        first = self._create_purchase(status=PurchaseRequest.Status.INVOICE)
        second = self._create_purchase(status=PurchaseRequest.Status.INVOICE)
        PurchaseSupportAttachment.objects.create(
            purchase=second,
            file=SimpleUploadedFile('demo.pdf', b'demo'),
        )

        with self.assertRaises(PurchaseBulkActionError):
            group_purchases_for_support(purchase_ids=[first.pk, second.pk])

    def test_group_support_allows_purchases_with_existing_support_type(self) -> None:
        first = self._create_purchase(
            status=PurchaseRequest.Status.INVOICE,
            support_document_type=self.support_type,
        )
        second = self._create_purchase(
            status=PurchaseRequest.Status.INVOICE,
            support_document_type=self.support_type,
        )

        code, leader_id, _, count = group_purchases_for_support(purchase_ids=[first.pk, second.pk])

        self.assertEqual(2, count)
        self.assertEqual(first.pk, leader_id)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(self.support_type, first.support_document_type)
        self.assertEqual(self.support_type, second.support_document_type)

    def test_ungroup_support_group_clears_relations(self) -> None:
        first = self._create_purchase(status=PurchaseRequest.Status.INVOICE)
        second = self._create_purchase(status=PurchaseRequest.Status.INVOICE)
        group_purchases_for_support(purchase_ids=[first.pk, second.pk])

        code, count, leader = ungroup_support_group(purchase_id=first.pk)

        self.assertIsNotNone(leader)
        self.assertTrue(code.startswith("SG-"))
        self.assertEqual(2, count)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual('', first.support_group_code)
        self.assertEqual('', second.support_group_code)
        self.assertIsNone(first.support_group_leader)
        self.assertIsNone(second.support_group_leader)

    def test_ungroup_support_group_requires_leader(self) -> None:
        first = self._create_purchase(status=PurchaseRequest.Status.INVOICE)
        second = self._create_purchase(status=PurchaseRequest.Status.INVOICE)
        group_purchases_for_support(purchase_ids=[first.pk, second.pk])

        with self.assertRaises(PurchaseBulkActionError):
            ungroup_support_group(purchase_id=second.pk)

    def _create_purchase(
        self,
        *,
        status: str,
        created_at: datetime | None = None,
        invoice_total: Decimal | None = None,
        payment_amount: Decimal = Decimal('0'),
        estimated_total: Decimal = Decimal('1'),
        support_document_type: SupportDocumentType | None = None,
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
            estimated_total=estimated_total,
            invoice_total=invoice_total,
            payment_amount=payment_amount,
            support_document_type=support_document_type,
        )
        if created_at:
            PurchaseRequest.objects.filter(pk=purchase.pk).update(created_at=created_at)
            purchase.refresh_from_db(fields=['created_at'])
        return purchase

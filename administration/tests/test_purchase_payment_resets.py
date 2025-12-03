from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from administration.models import PurchaseRequest, PurchasingExpenseType, Supplier
from administration.services.purchase_payment_resets import (
    get_purchases_missing_payment_amount_queryset,
    reset_missing_payment_amounts,
)


class PurchasePaymentResetsTests(TestCase):
    def setUp(self) -> None:
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            cedula='900101',
            password='test123',
            nombres='Reset',
            apellidos='Tester',
            telefono='3000000001',
            is_staff=True,
        )
        self.supplier = Supplier.objects.create(name='Proveedor Reset', tax_id='C456')
        self.category = PurchasingExpenseType.objects.create(name='Servicios')

    def test_reset_missing_payment_amounts_updates_records(self) -> None:
        affected = self._create_purchase(
            status=PurchaseRequest.Status.PAYMENT,
            invoice_total=Decimal('123456.78'),
            payment_amount=Decimal('0'),
        )
        unaffected = self._create_purchase(
            status=PurchaseRequest.Status.APPROVED,
            invoice_total=Decimal('99999'),
            payment_amount=Decimal('0'),
        )
        estimated_only = self._create_purchase(
            status=PurchaseRequest.Status.RECEPTION,
            invoice_total=Decimal('0'),
            payment_amount=Decimal('0'),
            estimated_total=Decimal('321'),
        )

        updated = reset_missing_payment_amounts()

        self.assertEqual(2, updated)
        affected.refresh_from_db()
        unaffected.refresh_from_db()
        estimated_only.refresh_from_db()
        self.assertEqual(Decimal('123456.78'), affected.payment_amount)
        self.assertEqual(Decimal('0'), unaffected.payment_amount)
        self.assertEqual(Decimal('321'), estimated_only.payment_amount)

    def test_get_queryset_filters_only_missing_payments(self) -> None:
        pending = self._create_purchase(
            status=PurchaseRequest.Status.RECEPTION,
            invoice_total=Decimal('0'),
            payment_amount=Decimal('0'),
            estimated_total=Decimal('5000'),
        )
        self._create_purchase(
            status=PurchaseRequest.Status.PAYMENT,
            invoice_total=Decimal('5000'),
            payment_amount=Decimal('5000'),
        )

        qs = get_purchases_missing_payment_amount_queryset()

        self.assertQuerysetEqual(qs.order_by('pk'), [pending.pk], transform=lambda obj: obj.pk)

    def _create_purchase(
        self,
        *,
        status: str,
        invoice_total: Decimal,
        payment_amount: Decimal,
        estimated_total: Decimal | None = None,
    ) -> PurchaseRequest:
        sequence = PurchaseRequest.objects.count() + 1
        return PurchaseRequest.objects.create(
            timeline_code=f'CMD-{sequence:05d}',
            name='Compra comandos',
            description='Reset',
            requester=self.user,
            supplier=self.supplier,
            expense_type=self.category,
            status=status,
            currency='COP',
            estimated_total=estimated_total if estimated_total is not None else invoice_total,
            invoice_total=invoice_total,
            payment_amount=payment_amount,
        )

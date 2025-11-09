from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from administration.models import PurchaseRequest, PurchasingExpenseType, Supplier, SupportDocumentType


class PurchaseAccountingPanelTests(TestCase):
    def setUp(self) -> None:
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            email='contabilidad@example.com',
            password='test123',
            is_staff=True,
        )
        self.client.force_login(self.user)
        self.supplier = Supplier.objects.create(
            name='Proveedor Contable',
            tax_id='900123456',
        )
        self.support_type = SupportDocumentType.objects.create(
            name='Factura electrónica',
            kind=SupportDocumentType.Kind.EXTERNAL,
        )
        self.expense_type = PurchasingExpenseType.objects.create(
            name='Gasto operativo',
            default_support_document_type=self.support_type,
        )
        self.purchase = PurchaseRequest.objects.create(
            timeline_code='SOL-0100',
            name='Compra lista para contabilidad',
            requester=self.user,
            supplier=self.supplier,
            expense_type=self.expense_type,
            support_document_type=self.support_type,
            status=PurchaseRequest.Status.PAYMENT,
            estimated_total=Decimal('250000.00'),
            payment_amount=Decimal('240000.00'),
        )

    def test_confirm_accounting_moves_purchase_to_archive(self) -> None:
        response = self.client.post(self._url(), data=self._payload())
        self.assertRedirects(
            response,
            f"{self._url()}?scope={PurchaseRequest.Status.ARCHIVED}",
            fetch_redirect_response=False,
        )
        self.purchase.refresh_from_db()
        self.assertEqual(PurchaseRequest.Status.ARCHIVED, self.purchase.status)
        self.assertTrue(self.purchase.accounted_in_system)
        self.assertIsNotNone(self.purchase.accounted_at)

    def test_accounting_blocked_for_invalid_status(self) -> None:
        self.purchase.status = PurchaseRequest.Status.RECEPTION
        self.purchase.save(update_fields=['status'])
        response = self.client.post(
            self._url(),
            data=self._payload(scope=PurchaseRequest.Status.RECEPTION),
        )
        self.assertRedirects(
            response,
            f"{self._url()}?scope={PurchaseRequest.Status.RECEPTION}&panel=accounting&purchase={self.purchase.pk}",
            fetch_redirect_response=False,
        )
        self.purchase.refresh_from_db()
        self.assertEqual(PurchaseRequest.Status.RECEPTION, self.purchase.status)
        self.assertFalse(self.purchase.accounted_in_system)

    def _url(self) -> str:
        return reverse('administration:purchases')

    def _payload(self, *, scope: str | None = None) -> dict[str, str]:
        payload = {
            'panel': 'accounting',
            'scope': scope or PurchaseRequest.Status.PAYMENT,
            'purchase': str(self.purchase.pk),
            'intent': 'confirm_accounting',
        }
        return payload

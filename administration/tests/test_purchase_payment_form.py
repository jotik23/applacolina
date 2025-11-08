from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from administration.models import PurchaseRequest, PurchasingExpenseType, Supplier, SupportDocumentType


class PurchasePaymentFormSubmissionTests(TestCase):
    def setUp(self) -> None:
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            email='staff@example.com',
            password='test123',
            is_staff=True,
        )
        self.client.force_login(self.user)
        self.supplier = Supplier.objects.create(
            name='Proveedor Demo',
            tax_id='900000001',
            account_holder_id='123',
            account_holder_name='Titular Demo',
            account_type='ahorros',
            account_number='000111222',
            bank_name='Banco Demo',
        )
        self.support_type = SupportDocumentType.objects.create(
            name='Factura electrónica',
            kind=SupportDocumentType.Kind.EXTERNAL,
        )
        self.expense_type = PurchasingExpenseType.objects.create(
            name='Insumos productivos',
            default_support_document_type=self.support_type,
        )
        self.purchase = PurchaseRequest.objects.create(
            timeline_code='SOL-0003',
            name='Compra lista para pago',
            requester=self.user,
            supplier=self.supplier,
            expense_type=self.expense_type,
            support_document_type=self.support_type,
            status=PurchaseRequest.Status.RECEPTION,
            payment_condition=PurchaseRequest.PaymentCondition.CREDIT,
            payment_method=PurchaseRequest.PaymentMethod.TRANSFER,
            estimated_total=Decimal('100000.00'),
        )

    def test_save_payment_updates_purchase_fields(self) -> None:
        response = self.client.post(self._url(), data=self._payload())
        self.assertRedirects(
            response,
            f"{self._url()}?scope={PurchaseRequest.Status.RECEPTION}",
            fetch_redirect_response=False,
        )
        self.purchase.refresh_from_db()
        self.supplier.refresh_from_db()
        self.assertEqual('555666', self.purchase.payment_account)
        self.assertIsNone(self.purchase.payment_date)
        self.assertEqual('Liberar recursos', self.purchase.payment_notes)
        self.assertEqual(PurchaseRequest.PaymentMethod.TRANSFER, self.purchase.payment_method)
        self.assertEqual('555666', self.purchase.supplier_account_number)
        self.assertEqual('Banco Uno', self.purchase.supplier_bank_name)
        self.assertEqual('555666', self.supplier.account_number)
        self.assertEqual('Banco Uno', self.supplier.bank_name)
        self.assertEqual(Decimal('95000.00'), self.purchase.payment_amount)

    def test_confirm_payment_moves_to_support_and_marks_credit_paid(self) -> None:
        response = self.client.post(
            self._url(),
            data=self._payload(intent='confirm_payment'),
        )
        self.assertRedirects(
            response,
            f"{self._url()}?scope={PurchaseRequest.Status.INVOICE}",
            fetch_redirect_response=False,
        )
        self.purchase.refresh_from_db()
        self.assertEqual(PurchaseRequest.Status.INVOICE, self.purchase.status)
        self.assertEqual(PurchaseRequest.PaymentCondition.CREDIT_PAID, self.purchase.payment_condition)
        self.assertEqual(timezone.localdate(), self.purchase.payment_date)

    def test_confirm_payment_blocked_when_amount_exceeds_estimate(self) -> None:
        response = self.client.post(
            self._url(),
            data=self._payload(intent='confirm_payment', payment_amount='120000'),
        )
        self.assertEqual(200, response.status_code)
        self.assertContains(response, "supera el total estimado")
        self.purchase.refresh_from_db()
        self.assertEqual(PurchaseRequest.Status.RECEPTION, self.purchase.status)

    def test_reopen_request_from_payment_panel(self) -> None:
        response = self.client.post(
            self._url(),
            data=self._payload(intent='reopen_request'),
        )
        self.assertRedirects(
            response,
            f"{self._url()}?scope={PurchaseRequest.Status.DRAFT}&panel=request&purchase={self.purchase.pk}",
            fetch_redirect_response=False,
        )
        self.purchase.refresh_from_db()
        self.assertEqual(PurchaseRequest.Status.DRAFT, self.purchase.status)

    def test_cash_payment_does_not_require_bank_information(self) -> None:
        response = self.client.post(
            self._url(),
            data=self._payload(
                payment_method=PurchaseRequest.PaymentMethod.CASH,
                payment_condition=PurchaseRequest.PaymentCondition.CASH,
                supplier_account_holder_name='',
                supplier_account_holder_id='',
                supplier_account_type='',
                supplier_account_number='',
                supplier_bank_name='',
            ),
        )
        self.assertRedirects(
            response,
            f"{self._url()}?scope={PurchaseRequest.Status.RECEPTION}",
            fetch_redirect_response=False,
        )
        self.purchase.refresh_from_db()
        self.assertEqual(PurchaseRequest.PaymentMethod.CASH, self.purchase.payment_method)
        self.assertEqual(timezone.localdate(), self.purchase.payment_date)

    def _url(self) -> str:
        return reverse('administration:purchases')

    def _payload(self, **extra: str) -> dict[str, str]:
        data = {
            'panel': 'payment',
            'scope': PurchaseRequest.Status.RECEPTION,
            'purchase': str(self.purchase.pk),
            'payment_amount': '95000',
            'payment_method': PurchaseRequest.PaymentMethod.TRANSFER,
            'payment_condition': PurchaseRequest.PaymentCondition.CREDIT,
            'payment_source': PurchaseRequest.PaymentSource.TREASURY,
            'payment_notes': 'Liberar recursos',
            'supplier_account_holder_name': 'Nuevo Titular',
            'supplier_account_holder_id': '9090',
            'supplier_account_type': 'corriente',
            'supplier_account_number': '555666',
            'supplier_bank_name': 'Banco Uno',
            'intent': 'save_payment',
        }
        data.update(extra)
        return data

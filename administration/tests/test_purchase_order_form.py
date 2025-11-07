from __future__ import annotations

from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from administration.models import PurchaseRequest, PurchasingExpenseType, Supplier, SupportDocumentType


class PurchaseOrderFormSubmissionTests(TestCase):
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
            tax_id='900999111',
            account_holder_id='123',
            account_holder_name='Titular Demo',
            account_type='ahorros',
            account_number='000111222',
            bank_name='Banco Demo',
        )
        self.support_type = SupportDocumentType.objects.create(
            name='Factura',
            kind=SupportDocumentType.Kind.EXTERNAL,
        )
        self.expense_type = PurchasingExpenseType.objects.create(
            name='CapEx',
            default_support_document_type=self.support_type,
        )
        self.purchase = PurchaseRequest.objects.create(
            timeline_code='SOL-0001',
            name='Compra pruebas',
            requester=self.user,
            supplier=self.supplier,
            expense_type=self.expense_type,
            support_document_type=self.support_type,
            status=PurchaseRequest.Status.APPROVED,
        )

    def test_save_order_updates_purchase_and_supplier(self) -> None:
        response = self.client.post(self._url(), data=self._payload())
        self.assertRedirects(
            response,
            f"{self._url()}?scope={PurchaseRequest.Status.APPROVED}",
            fetch_redirect_response=False,
        )
        self.purchase.refresh_from_db()
        self.supplier.refresh_from_db()
        self.assertEqual(date(2025, 1, 10), self.purchase.purchase_date)
        self.assertEqual(PurchaseRequest.PaymentCondition.CASH, self.purchase.payment_condition)
        self.assertEqual(PurchaseRequest.DeliveryCondition.SHIPPING, self.purchase.delivery_condition)
        self.assertEqual(date(2025, 1, 15), self.purchase.shipping_eta)
        self.assertEqual('Banco ACME', self.purchase.supplier_bank_name)
        self.assertEqual('999', self.supplier.account_holder_id)
        self.assertEqual('Banco ACME', self.supplier.bank_name)
        self.assertEqual(PurchaseRequest.Status.APPROVED, self.purchase.status)

    def test_confirm_order_with_shipping_moves_purchase_to_ordered(self) -> None:
        response = self.client.post(
            self._url(),
            data=self._payload(intent='confirm_order'),
        )
        self.assertRedirects(
            response,
            f"{self._url()}?scope={PurchaseRequest.Status.ORDERED}",
            fetch_redirect_response=False,
        )
        self.purchase.refresh_from_db()
        self.assertEqual(PurchaseRequest.Status.ORDERED, self.purchase.status)

    def test_confirm_order_with_credit_moves_purchase_to_payable(self) -> None:
        response = self.client.post(
            self._url(),
            data=self._payload(
                intent='confirm_order',
                payment_condition=PurchaseRequest.PaymentCondition.CREDIT,
            ),
        )
        self.assertRedirects(
            response,
            f"{self._url()}?scope={PurchaseRequest.Status.RECEPTION}",
            fetch_redirect_response=False,
        )
        self.purchase.refresh_from_db()
        self.assertEqual(PurchaseRequest.Status.RECEPTION, self.purchase.status)

    def test_cash_payment_does_not_require_bank_data(self) -> None:
        response = self.client.post(
            self._url(),
            data=self._payload(
                payment_method=PurchaseRequest.PaymentMethod.CASH,
                supplier_account_holder_name='',
                supplier_account_holder_id='',
                supplier_account_type='',
                supplier_account_number='',
                supplier_bank_name='',
            ),
        )
        self.assertRedirects(
            response,
            f"{self._url()}?scope={PurchaseRequest.Status.APPROVED}",
            fetch_redirect_response=False,
        )

    def test_reopen_request_moves_back_to_draft(self) -> None:
        self.purchase.status = PurchaseRequest.Status.ORDERED
        self.purchase.save(update_fields=['status'])
        response = self.client.post(
            self._url(),
            data={
                'panel': 'order',
                'scope': PurchaseRequest.Status.ORDERED,
                'purchase': str(self.purchase.pk),
                'intent': 'reopen_request',
            },
        )
        self.purchase.refresh_from_db()
        expected = f"{self._url()}?scope={PurchaseRequest.Status.DRAFT}&panel=request&purchase={self.purchase.pk}"
        self.assertRedirects(response, expected, fetch_redirect_response=False)
        self.assertEqual(PurchaseRequest.Status.DRAFT, self.purchase.status)

    def _url(self) -> str:
        return reverse('administration:purchases')

    def _payload(self, **extra: str) -> dict[str, str]:
        data = {
            'panel': 'order',
            'scope': PurchaseRequest.Status.APPROVED,
            'purchase': str(self.purchase.pk),
            'purchase_date': '2025-01-10',
            'delivery_condition': PurchaseRequest.DeliveryCondition.SHIPPING,
            'shipping_eta': '2025-01-15',
            'shipping_notes': 'Coordinar transporte',
            'payment_condition': PurchaseRequest.PaymentCondition.CASH,
            'payment_method': PurchaseRequest.PaymentMethod.TRANSFER,
            'payment_source': PurchaseRequest.PaymentSource.TBD,
            'supplier_account_holder_name': 'Nuevo Titular',
            'supplier_account_holder_id': '999',
            'supplier_account_type': 'corriente',
            'supplier_account_number': '222333444',
            'supplier_bank_name': 'Banco ACME',
            'intent': 'save_order',
        }
        data.update(extra)
        return data

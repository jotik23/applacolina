from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from administration.models import (
    PurchaseRequest,
    PurchasingExpenseType,
    PurchaseSupportAttachment,
    Supplier,
    SupportDocumentType,
)


class PurchaseInvoiceFormTests(TestCase):
    def setUp(self) -> None:
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            email='staff@example.com',
            password='test123',
            is_staff=True,
        )
        self.client.force_login(self.user)
        self.supplier = Supplier.objects.create(name='Proveedor Uno', tax_id='900111222')
        self.expense_type = PurchasingExpenseType.objects.create(name='Insumos generales')
        self.support_type_internal = SupportDocumentType.objects.create(
            name='Soporte interno',
            kind=SupportDocumentType.Kind.INTERNAL,
            template="""
                <div>
                    <h1>Soporte {{timeline_code}}</h1>
                    <p>Proveedor: {{supplier_name}}</p>
                </div>
            """,
        )
        self.purchase = PurchaseRequest.objects.create(
            timeline_code='SOL-0009',
            name='Compra insumos',
            supplier=self.supplier,
            expense_type=self.expense_type,
            status=PurchaseRequest.Status.INVOICE,
        )

    def test_save_invoice_updates_template_values_and_attachments(self) -> None:
        uploaded = SimpleUploadedFile('soporte.pdf', b'pdfcontent', content_type='application/pdf')
        payload = self._payload(intent='save_invoice')
        payload['template_fields[timeline_code]'] = 'SOL-TEST'
        payload['template_fields[supplier_name]'] = 'Proveedor Uno'
        payload['invoice_attachments'] = uploaded

        response = self.client.post(self._url(), data=payload, follow=False)

        expected_redirect = f"{self._url()}?scope={PurchaseRequest.Status.INVOICE}&panel=invoice&purchase={self.purchase.pk}"
        self.assertRedirects(response, expected_redirect, fetch_redirect_response=False)

        self.purchase.refresh_from_db()
        self.assertEqual(self.support_type_internal, self.purchase.support_document_type)
        self.assertEqual(
            {
                'timeline_code': 'SOL-TEST',
                'supplier_name': 'Proveedor Uno',
            },
            self.purchase.support_template_values,
        )
        self.assertEqual(1, PurchaseSupportAttachment.objects.filter(purchase=self.purchase).count())

    def test_confirm_invoice_moves_request_to_payment(self) -> None:
        payload = self._payload(intent='confirm_invoice')
        payload['template_fields[timeline_code]'] = 'SOL-TEST'
        response = self.client.post(self._url(), data=payload, follow=False)
        expected_redirect = f"{self._url()}?scope={PurchaseRequest.Status.PAYMENT}"
        self.assertRedirects(response, expected_redirect, fetch_redirect_response=False)
        self.purchase.refresh_from_db()
        self.assertEqual(PurchaseRequest.Status.PAYMENT, self.purchase.status)
        self.assertEqual(self.support_type_internal, self.purchase.support_document_type)

    def _url(self) -> str:
        return reverse('administration:purchases')

    def _payload(self, *, intent: str) -> dict[str, str]:
        return {
            'panel': 'invoice',
            'scope': PurchaseRequest.Status.INVOICE,
            'purchase': str(self.purchase.pk),
            'support_document_type': str(self.support_type_internal.pk),
            'intent': intent,
        }

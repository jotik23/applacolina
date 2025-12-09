from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from administration.models import (
    PurchaseItem,
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
            cedula='2001',
            password='test123',
            nombres='Staff',
            apellidos='Usuario',
            telefono='3000001000',
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

    def test_confirm_invoice_updates_group_members(self) -> None:
        self.purchase.support_group_code = 'SG-001'
        self.purchase.save(update_fields=['support_group_code'])
        follower = PurchaseRequest.objects.create(
            timeline_code='SOL-0010',
            name='Compra agrupada',
            supplier=self.supplier,
            expense_type=self.expense_type,
            status=PurchaseRequest.Status.INVOICE,
            support_group_code='SG-001',
            support_group_leader=self.purchase,
        )
        payload = self._payload(intent='confirm_invoice')
        response = self.client.post(self._url(), data=payload, follow=False)
        expected_redirect = f"{self._url()}?scope={PurchaseRequest.Status.PAYMENT}"
        self.assertRedirects(response, expected_redirect, fetch_redirect_response=False)
        follower.refresh_from_db()
        self.assertEqual(PurchaseRequest.Status.PAYMENT, follower.status)
        self.assertEqual(self.support_type_internal, follower.support_document_type)

    def test_group_follower_cannot_edit_support(self) -> None:
        self.purchase.support_group_code = 'SG-002'
        self.purchase.save(update_fields=['support_group_code'])
        follower = PurchaseRequest.objects.create(
            timeline_code='SOL-0011',
            name='Compra agrupada 2',
            supplier=self.supplier,
            expense_type=self.expense_type,
            status=PurchaseRequest.Status.INVOICE,
            support_group_code='SG-002',
            support_group_leader=self.purchase,
        )
        payload = self._payload(intent='save_invoice')
        payload['purchase'] = str(follower.pk)

        response = self.client.post(self._url(), data=payload, follow=False)

        self.assertEqual(200, response.status_code)
        self.assertContains(response, "Gestiona el soporte", status_code=200)
        follower.refresh_from_db()
        self.assertEqual(PurchaseRequest.Status.INVOICE, follower.status)
        self.assertIsNone(follower.support_document_type)

    def test_invoice_group_context_includes_summary(self) -> None:
        self.purchase.support_group_code = 'SG-010'
        self.purchase.invoice_total = Decimal('1000')
        self.purchase.payment_amount = Decimal('200')
        self.purchase.save(update_fields=['support_group_code', 'invoice_total', 'payment_amount'])
        follower = PurchaseRequest.objects.create(
            timeline_code='SOL-0020',
            name='Compra agrupada detalle',
            supplier=self.supplier,
            expense_type=self.expense_type,
            status=PurchaseRequest.Status.INVOICE,
            support_group_code='SG-010',
            support_group_leader=self.purchase,
            invoice_total=Decimal('500'),
            payment_amount=Decimal('100'),
        )
        PurchaseItem.objects.create(
            purchase=self.purchase,
            description='Item líder',
            quantity=Decimal('2'),
            estimated_amount=Decimal('150000'),
        )
        PurchaseItem.objects.create(
            purchase=follower,
            description='Item seguidor',
            quantity=Decimal('1'),
            estimated_amount=Decimal('80000'),
        )

        response = self.client.get(
            self._url(),
            {
                'scope': PurchaseRequest.Status.INVOICE,
                'panel': 'invoice',
                'purchase': self.purchase.pk,
            },
        )

        self.assertEqual(200, response.status_code)
        group = response.context['purchase_invoice_form']['group']
        self.assertTrue(group['can_ungroup'])
        self.assertEqual(Decimal('1500'), group['summary']['invoice_total'])
        self.assertEqual(Decimal('300'), group['summary']['payment_amount'])
        self.assertEqual(2, len(group['combined_items']))

        follower_group = self.client.get(
            self._url(),
            {
                'scope': PurchaseRequest.Status.INVOICE,
                'panel': 'invoice',
                'purchase': follower.pk,
            },
        ).context['purchase_invoice_form']['group']
        self.assertFalse(follower_group['can_ungroup'])
        self.assertTrue(follower_group['is_follower'])

    def test_post_ungroup_support_reverts_group(self) -> None:
        self.purchase.support_group_code = 'SG-011'
        self.purchase.save(update_fields=['support_group_code'])
        follower = PurchaseRequest.objects.create(
            timeline_code='SOL-0021',
            name='Compra para desagrupar',
            supplier=self.supplier,
            expense_type=self.expense_type,
            status=PurchaseRequest.Status.INVOICE,
            support_group_code='SG-011',
            support_group_leader=self.purchase,
        )

        response = self.client.post(
            self._url(),
            data={
                'panel': 'invoice',
                'scope': PurchaseRequest.Status.INVOICE,
                'purchase': str(self.purchase.pk),
                'intent': 'ungroup_support',
            },
            follow=False,
        )

        expected_redirect = (
            f"{self._url()}?scope={PurchaseRequest.Status.INVOICE}&panel=invoice&purchase={self.purchase.pk}"
        )
        self.assertRedirects(response, expected_redirect, fetch_redirect_response=False)
        self.purchase.refresh_from_db()
        follower.refresh_from_db()
        self.assertEqual('', self.purchase.support_group_code)
        self.assertEqual('', follower.support_group_code)


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

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from administration.models import PurchaseItem, PurchaseReceptionAttachment, PurchaseRequest, PurchasingExpenseType, Supplier


class PurchaseReceptionFormTests(TestCase):
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
        self.purchase = PurchaseRequest.objects.create(
            timeline_code='SOL-0002',
            name='Compra insumos',
            supplier=self.supplier,
            expense_type=self.expense_type,
            status=PurchaseRequest.Status.ORDERED,
        )
        self.item = PurchaseItem.objects.create(
            purchase=self.purchase,
            description='Bultos concentrado',
            quantity=Decimal('10'),
            estimated_amount=Decimal('50000'),
        )

    def test_save_reception_updates_item_quantities(self) -> None:
        response = self.client.post(
            self._url(),
            data=self._payload(received='6'),
        )
        self.assertRedirects(
            response,
            f"{self._url()}?scope={PurchaseRequest.Status.ORDERED}",
            fetch_redirect_response=False,
        )
        self.item.refresh_from_db()
        self.purchase.refresh_from_db()
        self.assertEqual(Decimal('6'), self.item.received_quantity)
        self.assertEqual('', self.purchase.reception_notes)
        self.assertEqual(PurchaseRequest.Status.ORDERED, self.purchase.status)

    def test_confirm_reception_changes_status_and_saves_notes(self) -> None:
        uploaded = SimpleUploadedFile("remision.txt", b"ok", content_type="text/plain")
        payload = self._payload(received='10', intent='confirm_reception', notes='Recepción completa')
        payload['reception_attachments'] = uploaded
        response = self.client.post(self._url(), data=payload, follow=False)
        self.assertEqual(302, response.status_code)
        self.purchase.refresh_from_db()
        self.item.refresh_from_db()
        self.assertEqual(PurchaseRequest.Status.RECEPTION, self.purchase.status)
        self.assertEqual('Recepción completa', self.purchase.reception_notes)
        self.assertEqual(Decimal('10'), self.item.received_quantity)
        self.assertEqual(1, PurchaseReceptionAttachment.objects.filter(purchase=self.purchase).count())

    def _url(self) -> str:
        return reverse('administration:purchases')

    def _payload(self, *, received: str, intent: str = 'save_reception', notes: str = '') -> dict[str, str]:
        return {
            'panel': 'reception',
            'scope': PurchaseRequest.Status.ORDERED,
            'purchase': str(self.purchase.pk),
            'receipts[0][item_id]': str(self.item.pk),
            'receipts[0][received_quantity]': received,
            'reception_notes': notes,
            'intent': intent,
        }

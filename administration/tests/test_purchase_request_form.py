from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from administration.models import (
    ExpenseTypeApprovalRule,
    Product,
    PurchaseItem,
    PurchaseRequest,
    PurchasingExpenseType,
    Supplier,
    SupportDocumentType,
)
from production.models import ChickenHouse, Farm


class PurchaseRequestFormSubmissionTests(TestCase):
    def setUp(self) -> None:
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            email='staff@example.com',
            password='test123',
            is_staff=True,
        )
        self.approver = user_model.objects.create_user(
            email='approver@example.com',
            password='test123',
            is_staff=True,
        )
        self.client.force_login(self.user)
        self.supplier = Supplier.objects.create(name='Proveedor Demo', tax_id='900123456')
        self.support_type = SupportDocumentType.objects.create(
            name='Factura Electrónica',
            kind=SupportDocumentType.Kind.EXTERNAL,
        )
        self.expense_type = PurchasingExpenseType.objects.create(
            name='CapEx Granjas',
            default_support_document_type=self.support_type,
        )
        self.farm = Farm.objects.create(name='Granja 1')
        self.house = ChickenHouse.objects.create(name='Galpón 1', farm=self.farm)
        self.product = Product.objects.create(name='Motor ventilador', unit='Unidad')

    def test_create_purchase_request_in_draft(self) -> None:
        response = self.client.post(self._url(), data=self._base_payload())
        expected_redirect = f"{self._url()}?scope={PurchaseRequest.Status.DRAFT}"
        self.assertRedirects(response, expected_redirect, fetch_redirect_response=False)

        purchase = PurchaseRequest.objects.get(name='Compra equipos críticos')
        self.assertEqual(PurchaseRequest.Status.DRAFT, purchase.status)
        self.assertEqual(self.user, purchase.requester)
        self.assertEqual(Decimal('1200000'), purchase.estimated_total)
        self.assertEqual(self.farm, purchase.scope_farm)
        self.assertEqual('', purchase.scope_batch_code)
        self.assertEqual(self.support_type, purchase.support_document_type)
        self.assertEqual(PurchaseRequest.AreaScope.CHICKEN_HOUSE, purchase.scope_area)
        self.assertEqual(1, purchase.items.count())
        item = purchase.items.first()
        assert item is not None
        self.assertEqual(Decimal('2'), item.quantity)

    def test_send_purchase_request_runs_workflow(self) -> None:
        ExpenseTypeApprovalRule.objects.create(
            expense_type=self.expense_type,
            approver=self.approver,
        )
        payload = self._base_payload() | {'intent': 'send_workflow'}
        response = self.client.post(self._url(), data=payload)
        expected_redirect = f"{self._url()}?scope={PurchaseRequest.Status.SUBMITTED}"
        self.assertRedirects(response, expected_redirect, fetch_redirect_response=False)

        purchase = PurchaseRequest.objects.get(name='Compra equipos críticos')
        self.assertEqual(PurchaseRequest.Status.SUBMITTED, purchase.status)
        self.assertEqual(1, purchase.approvals.count())

    def test_update_existing_purchase_replaces_items(self) -> None:
        purchase = PurchaseRequest.objects.create(
            timeline_code='SOL-LEGACY',
            name='Compra legacy',
            requester=self.user,
            supplier=self.supplier,
            expense_type=self.expense_type,
        )
        existing_item = PurchaseItem.objects.create(
            purchase=purchase,
            description='Motor viejo',
            quantity=Decimal('1'),
            estimated_amount=Decimal('500000'),
        )
        payload = self._base_payload() | {
            'purchase': str(purchase.pk),
            'items[0][id]': str(existing_item.pk),
            'items[0][description]': 'Motor actualizado',
            'items[1][description]': 'Sistema eléctrico',
            'items[1][quantity]': '3',
            'items[1][estimated_amount]': '450000',
        }

        response = self.client.post(self._url(), data=payload)
        expected_redirect = f"{self._url()}?scope={PurchaseRequest.Status.DRAFT}"
        self.assertRedirects(response, expected_redirect, fetch_redirect_response=False)

        purchase.refresh_from_db()
        self.assertEqual('SOL-LEGACY', purchase.timeline_code)
        self.assertEqual(2, purchase.items.count())
        self.assertTrue(purchase.items.filter(description='Motor actualizado').exists())
        self.assertTrue(purchase.items.filter(description='Sistema eléctrico').exists())
        self.assertEqual(Decimal('1650000'), purchase.estimated_total)
        self.assertEqual(self.house, purchase.scope_chicken_house)
        self.assertEqual(self.support_type, purchase.support_document_type)
        self.assertEqual(PurchaseRequest.AreaScope.CHICKEN_HOUSE, purchase.scope_area)

    def test_reopen_from_submitted_returns_to_draft(self) -> None:
        purchase = PurchaseRequest.objects.create(
            timeline_code='SOL-0005',
            name='Compra en aprobación',
            requester=self.user,
            supplier=self.supplier,
            expense_type=self.expense_type,
            support_document_type=self.support_type,
            status=PurchaseRequest.Status.SUBMITTED,
        )
        response = self.client.post(
            self._url(),
            data={
                'panel': 'request',
                'scope': PurchaseRequest.Status.SUBMITTED,
                'purchase': str(purchase.pk),
                'intent': 'reopen_request',
            },
        )
        expected = f"{self._url()}?scope={PurchaseRequest.Status.DRAFT}&panel=request&purchase={purchase.pk}"
        self.assertRedirects(response, expected, fetch_redirect_response=False)
        purchase.refresh_from_db()
        self.assertEqual(PurchaseRequest.Status.DRAFT, purchase.status)

    def test_company_area_selection_clears_scope(self) -> None:
        payload = self._base_payload() | {
            'scope_area': PurchaseRequest.AreaScope.COMPANY,
            'scope_farm_id': '',
            'scope_chicken_house_id': '',
        }
        response = self.client.post(self._url(), data=payload)
        expected_redirect = f"{self._url()}?scope={PurchaseRequest.Status.DRAFT}"
        self.assertRedirects(response, expected_redirect, fetch_redirect_response=False)

        purchase = PurchaseRequest.objects.get(name='Compra equipos críticos')
        self.assertEqual(PurchaseRequest.AreaScope.COMPANY, purchase.scope_area)
        self.assertIsNone(purchase.scope_farm)
        self.assertIsNone(purchase.scope_chicken_house)

    def test_item_can_reference_existing_product(self) -> None:
        payload = self._base_payload() | {
            'items[0][description]': '',
            'items[0][product_id]': str(self.product.pk),
        }
        response = self.client.post(self._url(), data=payload)
        expected_redirect = f"{self._url()}?scope={PurchaseRequest.Status.DRAFT}"
        self.assertRedirects(response, expected_redirect, fetch_redirect_response=False)

        purchase = PurchaseRequest.objects.get(name='Compra equipos críticos')
        item = purchase.items.first()
        assert item is not None
        self.assertEqual(self.product, item.product)
        self.assertEqual(self.product.name, item.description)

    def test_invalid_product_reference_does_not_submit(self) -> None:
        payload = self._base_payload() | {
            'items[0][description]': '',
            'items[0][product_id]': '99999',
        }
        response = self.client.post(self._url(), data=payload)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "El producto seleccionado ya no existe.", status_code=200)
        self.assertFalse(PurchaseRequest.objects.filter(name='Compra equipos críticos').exists())

    def _url(self) -> str:
        return reverse('administration:purchases')

    def _base_payload(self) -> dict[str, str]:
        return {
            'panel': 'request',
            'scope': PurchaseRequest.Status.DRAFT,
            'summary': 'Compra equipos críticos',
            'supplier': str(self.supplier.pk),
            'expense_type': str(self.expense_type.pk),
            'items[0][description]': 'Motor ventilador',
            'items[0][quantity]': '2',
            'items[0][estimated_amount]': '1200000',
            'scope_farm_id': str(self.farm.pk),
            'scope_chicken_house_id': str(self.house.pk),
            'scope_batch_code': '',
            'scope_area': f'{PurchaseRequest.AreaScope.CHICKEN_HOUSE}:{self.house.pk}',
        }

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from administration.models import Product


class ProductManagementViewTests(TestCase):
    def setUp(self) -> None:
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            email='manager@example.com',
            password='test123',
            is_staff=True,
        )
        self.client.force_login(self.user)
        self.product = Product.objects.create(name='Motor', unit='Unidad')

    def test_list_products(self) -> None:
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Motor')
        self.assertContains(response, 'Unidad')

    def test_create_product(self) -> None:
        payload = {
            'form_action': 'product',
            'name': 'Banda transportadora',
            'unit': 'Bultos',
        }
        response = self.client.post(self._url(), data=payload)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Product.objects.filter(name='Banda transportadora', unit='Bultos').exists())

    def test_update_product(self) -> None:
        payload = {
            'form_action': 'product',
            'product_id': str(self.product.pk),
            'name': 'Motor reforzado',
            'unit': 'Paquete x 100',
        }
        response = self.client.post(self._url(), data=payload)
        self.assertEqual(response.status_code, 302)
        self.product.refresh_from_db()
        self.assertEqual('Motor reforzado', self.product.name)
        self.assertEqual('Paquete x 100', self.product.unit)

    def test_delete_product(self) -> None:
        payload = {
            'form_action': 'delete',
            'product_id': str(self.product.pk),
        }
        response = self.client.post(self._url(), data=payload)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Product.objects.filter(pk=self.product.pk).exists())

    def _url(self) -> str:
        return reverse('configuration:products')

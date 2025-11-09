from __future__ import annotations

import json
from decimal import Decimal

from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from administration.models import PurchaseRequest, PurchasingExpenseType, Supplier
from personal.models import UserProfile


class MiniAppPurchaseRequestViewTests(TestCase):
    def setUp(self):
        self.user = UserProfile.objects.create_user(
            cedula="1001",
            password="testpass123",
            nombres="Laura",
            apellidos="Test",
            telefono="3001234567",
        )
        access_perm = Permission.objects.get(codename="access_mini_app")
        overview_perm = Permission.objects.get(codename="view_mini_app_purchase_overview_card")
        self.user.user_permissions.add(access_perm, overview_perm)
        self.client.force_login(self.user)
        self.expense_type = PurchasingExpenseType.objects.create(name="Bioseguridad")
        self.supplier = Supplier.objects.create(name="Proveedor Uno", tax_id="900111222")
        self.url = reverse("task_manager:mini-app-purchase-request")

    def test_view_requires_permission(self):
        self.user.user_permissions.clear()
        response = self.client.post(
            self.url,
            data=json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("error", response.json())

    def test_submit_purchase_creates_draft(self):
        payload = {
            "intent": "save_draft",
            "summary": "Compra de guantes",
            "expense_type_id": self.expense_type.pk,
            "supplier_id": self.supplier.pk,
            "area": {"scope": PurchaseRequest.AreaScope.COMPANY, "farm_id": None, "chicken_house_id": None},
            "items": [
                {"description": "Guantes nitrilo", "quantity": "2", "estimated_amount": "12500"},
            ],
        }
        response = self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")
        self.assertIn("purchase", data)
        purchase = PurchaseRequest.objects.get()
        self.assertEqual(purchase.name, "Compra de guantes")
        self.assertEqual(purchase.requester, self.user)
        self.assertEqual(purchase.expense_type, self.expense_type)
        self.assertEqual(purchase.supplier, self.supplier)
        self.assertEqual(purchase.status, PurchaseRequest.Status.DRAFT)
        self.assertEqual(purchase.estimated_total, Decimal("25000"))

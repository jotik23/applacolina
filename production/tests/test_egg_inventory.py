from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from production.models import (
    BirdBatch,
    BreedReference,
    EggClassificationEntry,
    Farm,
    ProductionRecord,
)


class EggInventoryDashboardTests(TestCase):
    def setUp(self) -> None:
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            cedula="inventory-admin",
            password="strongpass",
            nombres="Inventory",
            apellidos="Admin",
            telefono="3000002000",
            is_staff=True,
        )
        self.client.force_login(self.user)
        self.farm = Farm.objects.create(name="Central")
        self.breed = BreedReference.objects.create(name="Hy-Line")
        self.batch = BirdBatch.objects.create(
            farm=self.farm,
            status=BirdBatch.Status.ACTIVE,
            birth_date=date.today(),
            initial_quantity=1200,
            breed=self.breed,
        )
        self.record = ProductionRecord.objects.create(
            bird_batch=self.batch,
            date=date.today(),
            production=Decimal("150.0"),
            consumption=Decimal("100"),
            mortality=3,
            discard=1,
        )

    def test_dashboard_renders_pending_batches(self) -> None:
        response = self.client.get(reverse("production:egg-inventory"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "production/egg_inventory.html")
        self.assertIn("pending_batches", response.context)
        self.assertTrue(response.context["pending_batches"])

    def test_confirm_receipt_updates_batch(self) -> None:
        batch = self.record.egg_classification
        response = self.client.post(
            reverse("production:egg-inventory-batch", args=[batch.pk]),
            {
                "form": "receipt",
                "batch_id": batch.pk,
                "received_cartons": "148.5",
                "notes": "Diferencia menor.",
            },
        )
        self.assertRedirects(response, reverse("production:egg-inventory-batch", args=[batch.pk]))
        batch.refresh_from_db()
        self.assertEqual(batch.received_cartons, Decimal("148.5"))
        self.assertEqual(batch.notes, "Diferencia menor.")
        self.assertEqual(batch.status, batch.Status.CONFIRMED)

    def test_classification_requires_confirmation(self) -> None:
        batch = self.record.egg_classification
        response = self.client.post(
            reverse("production:egg-inventory-batch", args=[batch.pk]),
            {
                "form": "classification",
                "batch_id": batch.pk,
                "type_jumbo": "80",
                "type_aaa": "70",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["classification_form"].errors or response.context["classification_form"].non_field_errors())
        self.assertFalse(EggClassificationEntry.objects.exists())

    def test_classification_flow_creates_entries(self) -> None:
        batch = self.record.egg_classification
        self.client.post(
            reverse("production:egg-inventory-batch", args=[batch.pk]),
            {
                "form": "receipt",
                "batch_id": batch.pk,
                "received_cartons": "150.0",
            },
        )
        response = self.client.post(
            reverse("production:egg-inventory-batch", args=[batch.pk]),
            {
                "form": "classification",
                "batch_id": batch.pk,
                "type_jumbo": "50",
                "type_aaa": "50",
                "type_aa": "50",
            },
        )
        self.assertRedirects(response, reverse("production:egg-inventory-batch", args=[batch.pk]))
        batch.refresh_from_db()
        entries = EggClassificationEntry.objects.filter(batch=batch)
        self.assertEqual(entries.count(), 3)
        self.assertEqual(sum(entry.cartons for entry in entries), Decimal("150"))
        self.assertEqual(batch.status, batch.Status.CLASSIFIED)
        self.assertEqual(batch.classified_total, Decimal("150"))

from __future__ import annotations

from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from production.models import BirdBatch, BirdBatchRoomAllocation, ChickenHouse, Farm, Room


class BatchManagementViewTests(TestCase):
    """Smoke tests for batch management and allocation flows."""

    def setUp(self) -> None:
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="batch-admin",
            email="batch-admin@example.com",
            password="supersecure",
            is_staff=True,
        )
        self.client.force_login(self.user)
        self.farm = Farm.objects.create(name="Granja Central")
        self.chicken_house = ChickenHouse.objects.create(
            farm=self.farm,
            name="Galpón A",
        )
        self.room = Room.objects.create(
            chicken_house=self.chicken_house,
            name="Salón 1",
            area_m2=300,
        )

    def test_get_batch_dashboard(self) -> None:
        response = self.client.get(reverse("production:batches"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "production/batches.html")
        self.assertIn("batch_cards", response.context)
        self.assertIn("batch_metrics", response.context)
        self.assertIsNone(response.context["selected_batch"])

    def test_create_batch(self) -> None:
        response = self.client.post(
            reverse("production:batches"),
            {
                "form_type": "batch",
                "farm": self.farm.pk,
                "status": BirdBatch.Status.ACTIVE,
                "birth_date": date.today(),
                "initial_quantity": 1500,
                "breed": "Hy-Line Brown",
            },
        )
        self.assertEqual(response.status_code, 302)
        batch = BirdBatch.objects.get()
        self.assertEqual(batch.initial_quantity, 1500)
        self.assertIn(f"batch={batch.pk}", response.url)

    def test_distribution_creates_allocations(self) -> None:
        batch = BirdBatch.objects.create(
            farm=self.farm,
            status=BirdBatch.Status.ACTIVE,
            birth_date=date.today(),
            initial_quantity=1800,
            breed="Hy-Line Brown",
        )
        field_name = f"room_{self.room.pk}"
        response = self.client.post(
            reverse("production:batches"),
            {
                "form_type": "distribution",
                "batch_id": batch.pk,
                field_name: "900",
            },
        )
        self.assertEqual(response.status_code, 302)
        allocation = BirdBatchRoomAllocation.objects.get()
        self.assertEqual(allocation.quantity, 900)
        self.assertIn(f"batch={batch.pk}", response.url)

    def test_distribution_updates_and_removes(self) -> None:
        batch = BirdBatch.objects.create(
            farm=self.farm,
            status=BirdBatch.Status.ACTIVE,
            birth_date=date.today(),
            initial_quantity=1800,
            breed="Hy-Line Brown",
        )
        allocation = BirdBatchRoomAllocation.objects.create(
            bird_batch=batch,
            room=self.room,
            quantity=500,
        )
        field_name = f"room_{self.room.pk}"
        response = self.client.post(
            reverse("production:batches"),
            {
                "form_type": "distribution",
                "batch_id": batch.pk,
                field_name: "1200",
            },
        )
        self.assertRedirects(response, f"{reverse('production:batches')}?batch={batch.pk}")
        allocation.refresh_from_db()
        self.assertEqual(allocation.quantity, 1200)

        response = self.client.post(
            reverse("production:batches"),
            {
                "form_type": "distribution",
                "batch_id": batch.pk,
                field_name: "0",
            },
        )
        self.assertRedirects(response, f"{reverse('production:batches')}?batch={batch.pk}")
        self.assertFalse(BirdBatchRoomAllocation.objects.filter(pk=allocation.pk).exists())

    def test_distribution_validation_prevents_overflow(self) -> None:
        batch = BirdBatch.objects.create(
            farm=self.farm,
            status=BirdBatch.Status.ACTIVE,
            birth_date=date.today(),
            initial_quantity=1000,
            breed="Hy-Line Brown",
        )
        response = self.client.post(
            reverse("production:batches"),
            {
                "form_type": "distribution",
                "batch_id": batch.pk,
                f"room_{self.room.pk}": "2000",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "La suma asignada supera la cantidad inicial del lote")
        self.assertFalse(BirdBatchRoomAllocation.objects.filter(bird_batch=batch).exists())

    def test_update_batch_view(self) -> None:
        batch = BirdBatch.objects.create(
            farm=self.farm,
            status=BirdBatch.Status.ACTIVE,
            birth_date=date.today(),
            initial_quantity=1800,
            breed="Hy-Line Brown",
        )
        response = self.client.get(reverse("production:batch-update", args=[batch.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "production/batch_form.html")

    def test_delete_allocation_flow(self) -> None:
        batch = BirdBatch.objects.create(
            farm=self.farm,
            status=BirdBatch.Status.ACTIVE,
            birth_date=date.today(),
            initial_quantity=1800,
            breed="Hy-Line Brown",
        )
        allocation = BirdBatchRoomAllocation.objects.create(
            bird_batch=batch,
            room=self.room,
            quantity=500,
        )
        response = self.client.post(reverse("production:batch-allocation-delete", args=[allocation.pk]))
        self.assertRedirects(response, reverse("production:batches"))
        self.assertFalse(BirdBatchRoomAllocation.objects.filter(pk=allocation.pk).exists())

    def test_delete_batch_flow(self) -> None:
        batch = BirdBatch.objects.create(
            farm=self.farm,
            status=BirdBatch.Status.ACTIVE,
            birth_date=date.today(),
            initial_quantity=1800,
            breed="Hy-Line Brown",
        )
        response = self.client.post(reverse("production:batch-delete", args=[batch.pk]))
        self.assertRedirects(response, reverse("production:batches"))
        self.assertFalse(BirdBatch.objects.filter(pk=batch.pk).exists())

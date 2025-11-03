from __future__ import annotations

from datetime import date, timedelta

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

    def test_batch_cards_order_and_labels(self) -> None:
        today = date.today()
        oldest_birth = today - timedelta(days=70)
        same_age_birth = oldest_birth
        newer_birth = today - timedelta(days=28)

        oldest_with_more_birds = BirdBatch.objects.create(
            farm=self.farm,
            status=BirdBatch.Status.ACTIVE,
            birth_date=same_age_birth,
            initial_quantity=2400,
            breed="Hy-Line Brown",
        )
        oldest_with_less_birds = BirdBatch.objects.create(
            farm=self.farm,
            status=BirdBatch.Status.ACTIVE,
            birth_date=oldest_birth,
            initial_quantity=1800,
            breed="Hy-Line Brown",
        )
        younger_batch = BirdBatch.objects.create(
            farm=self.farm,
            status=BirdBatch.Status.ACTIVE,
            birth_date=newer_birth,
            initial_quantity=2600,
            breed="Hy-Line Brown",
        )
        inactive_batch = BirdBatch.objects.create(
            farm=self.farm,
            status=BirdBatch.Status.INACTIVE,
            birth_date=today - timedelta(days=90),
            initial_quantity=3000,
            breed="Hy-Line Brown",
        )

        response = self.client.get(reverse("production:batches"))
        self.assertEqual(response.status_code, 200)

        batch_cards = response.context["batch_cards"]
        self.assertGreaterEqual(len(batch_cards), 4)

        active_order = [card["id"] for card in batch_cards if card["status"] == BirdBatch.Status.ACTIVE]
        self.assertEqual(
            active_order[:3],
            [
                oldest_with_more_birds.pk,
                oldest_with_less_birds.pk,
                younger_batch.pk,
            ],
        )

        active_labels = [card["label"] for card in batch_cards if card["status"] == BirdBatch.Status.ACTIVE]
        self.assertEqual(active_labels[:3], ["Lote #1", "Lote #2", "Lote #3"])

        inactive_card = next(card for card in batch_cards if card["id"] == inactive_batch.pk)
        self.assertEqual(inactive_card["label"], f"Lote #{inactive_batch.pk}")

        selected_batch = response.context["selected_batch"]
        self.assertIsNotNone(selected_batch)
        self.assertEqual(selected_batch.pk, oldest_with_more_birds.pk)
        self.assertEqual(response.context["selected_batch_label"], "Lote #1")

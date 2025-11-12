from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from production.models import BreedReference, BreedWeeklyGuide
from production.services.reference_tables import (
    get_reference_targets,
    reset_reference_targets_cache,
)


class ReferenceTablesViewTests(TestCase):
    def setUp(self) -> None:
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            cedula="ref-admin",
            password="ultrasecure",
            nombres="Ref",
            apellidos="Admin",
            telefono="3000003000",
            is_staff=True,
        )
        self.client.force_login(self.user)
        reset_reference_targets_cache()

    def test_render_reference_page_without_breeds(self) -> None:
        response = self.client.get(reverse("production:reference-tables"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "production/reference_tables.html")
        self.assertIn("breed_form", response.context)
        self.assertEqual(response.context["breeds"], [])

    def test_create_breed_action(self) -> None:
        response = self.client.post(
            reverse("production:reference-tables"),
            {"action": "create-breed", "name": "Hy-Line Brown"},
        )
        self.assertEqual(response.status_code, 302)
        breed = BreedReference.objects.get(name="Hy-Line Brown")
        self.assertIn(str(breed.pk), response.url)

    def test_update_metrics_persists_week(self) -> None:
        breed = BreedReference.objects.create(name="Guía Elite")
        payload = {
            "action": "update-metrics",
            "breed_id": str(breed.pk),
            "posture_percentage_1": "85.5",
            "grams_per_bird_1": "95.2",
            "egg_weight_g_1": "60.0",
            "weekly_mortality_percentage_1": "2.1",
        }
        response = self.client.post(reverse("production:reference-tables"), payload)
        self.assertEqual(response.status_code, 302)
        entry = BreedWeeklyGuide.objects.get(breed=breed, week=1)
        self.assertEqual(entry.posture_percentage, Decimal("85.5"))
        self.assertEqual(entry.grams_per_bird, Decimal("95.2"))
        self.assertEqual(entry.egg_weight_g, Decimal("60.0"))
        self.assertEqual(entry.weekly_mortality_percentage, Decimal("2.1"))

    def test_reference_targets_use_weekly_table(self) -> None:
        breed = BreedReference.objects.create(name="Hy-Line Brown")
        BreedWeeklyGuide.objects.create(
            breed=breed,
            week=5,
            grams_per_bird=Decimal("95"),
            egg_weight_g=Decimal("63"),
            posture_percentage=Decimal("92"),
            weekly_mortality_percentage=Decimal("2.8"),
        )
        reset_reference_targets_cache()

        targets = get_reference_targets(breed, age_weeks=5, current_birds=10000)

        self.assertEqual(targets["production_percent"], 92.0)
        self.assertEqual(targets["egg_weight_g"], 63.0)
        self.assertEqual(targets["consumption_kg"], 950.0)
        self.assertEqual(targets["mortality_birds"], 40.0)
        self.assertGreater(targets["discard_birds"], 0.0)

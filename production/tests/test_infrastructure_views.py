from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from production.models import ChickenHouse, Farm, Room


class InfrastructureViewTests(TestCase):
    """Smoke tests for the poultry infrastructure management flows."""

    def setUp(self) -> None:
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            cedula="infra-admin",
            password="supersecure",
            nombres="Infra",
            apellidos="Admin",
            telefono="3000002000",
            is_staff=True,
        )
        self.client.force_login(self.user)

    def test_get_infrastructure_page(self) -> None:
        response = self.client.get(reverse("production:infrastructure"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "production/infrastructure.html")
        self.assertIn("farm_form", response.context)
        self.assertIn("infrastructure_stats", response.context)

    def test_create_farm_from_dashboard(self) -> None:
        response = self.client.post(
            reverse("production:infrastructure"),
            {"form_type": "farm", "name": "Granja Andina"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Farm.objects.filter(name="Granja Andina").exists())
        expected_prefix = reverse("production:infrastructure")
        self.assertTrue(response.url.startswith(f"{expected_prefix}?panel=farm"))

    def test_create_chicken_house(self) -> None:
        farm = Farm.objects.create(name="Granja Central")
        response = self.client.post(
            reverse("production:infrastructure"),
            {
                "form_type": "chicken_house",
                "farm": farm.pk,
                "name": "Galpón A",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(ChickenHouse.objects.filter(name="Galpón A", farm=farm).exists())
        self.assertIn("?panel=chicken_house", response.url)
        self.assertIn(f"farm={farm.pk}", response.url)

    def test_create_room(self) -> None:
        farm = Farm.objects.create(name="Granja Central")
        barn = ChickenHouse.objects.create(farm=farm, name="Galpón A")
        response = self.client.post(
            reverse("production:infrastructure"),
            {
                "form_type": "room",
                "chicken_house": barn.pk,
                "name": "Salón 1",
                "area_m2": "300.0",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Room.objects.filter(name="Salón 1", chicken_house=barn).exists())
        self.assertIn("?panel=room", response.url)
        self.assertIn(f"chicken_house={barn.pk}", response.url)

    def test_update_views_render(self) -> None:
        farm = Farm.objects.create(name="Granja Central")
        barn = ChickenHouse.objects.create(farm=farm, name="Galpón A")
        room = Room.objects.create(chicken_house=barn, name="Salón 1", area_m2=50)

        urls = {
            "farm": reverse("production:farm-update", args=[farm.pk]),
            "chicken_house": reverse("production:chicken-house-update", args=[barn.pk]),
            "room": reverse("production:room-update", args=[room.pk]),
        }

        for label, url in urls.items():
            with self.subTest(entity=label):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, "production/infrastructure_form.html")

    def test_delete_room_flow(self) -> None:
        farm = Farm.objects.create(name="Granja Central")
        barn = ChickenHouse.objects.create(farm=farm, name="Galpón A")
        room = Room.objects.create(chicken_house=barn, name="Salón 1", area_m2=50)

        response = self.client.post(reverse("production:room-delete", args=[room.pk]))
        self.assertRedirects(response, reverse("production:infrastructure"))
        self.assertFalse(Room.objects.filter(pk=room.pk).exists())

    def test_stats_compute_area_from_rooms(self) -> None:
        baseline = self.client.get(reverse("production:infrastructure"))
        baseline_total = baseline.context["infrastructure_stats"]["total_house_area"]

        farm = Farm.objects.create(name="Granja Central")
        primary_barn = ChickenHouse.objects.create(farm=farm, name="Galpón A")
        secondary_barn = ChickenHouse.objects.create(farm=farm, name="Galpón B")
        Room.objects.create(chicken_house=primary_barn, name="Salón 1", area_m2=50)
        Room.objects.create(chicken_house=secondary_barn, name="Salón 2", area_m2=75)

        response = self.client.get(reverse("production:infrastructure"))
        stats = response.context["infrastructure_stats"]

        self.assertEqual(stats["total_house_area"], baseline_total + Decimal("125"))

        farms = response.context["farms"]
        context_farm = farms.get(pk=farm.pk)
        self.assertIsNotNone(context_farm)
        assert context_farm is not None
        self.assertEqual(context_farm.area_m2, Decimal("125"))
        barns = {barn.name: barn for barn in context_farm.chicken_houses.all()}
        self.assertEqual(barns["Galpón A"].area_m2, Decimal("50"))
        self.assertEqual(barns["Galpón B"].area_m2, Decimal("75"))

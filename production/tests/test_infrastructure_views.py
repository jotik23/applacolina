from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from production.models import ChickenHouse, Farm, Room


class InfrastructureViewTests(TestCase):
    """Smoke tests for the poultry infrastructure management flows."""

    def setUp(self) -> None:
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="infra-admin",
            email="infra-admin@example.com",
            password="supersecure",
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
                "area_m2": "1200.5",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(ChickenHouse.objects.filter(name="Galpón A", farm=farm).exists())
        self.assertIn("?panel=chicken_house", response.url)
        self.assertIn(f"farm={farm.pk}", response.url)

    def test_create_room(self) -> None:
        farm = Farm.objects.create(name="Granja Central")
        barn = ChickenHouse.objects.create(farm=farm, name="Galpón A", area_m2=1200)
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
        barn = ChickenHouse.objects.create(farm=farm, name="Galpón A", area_m2=100)
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
        barn = ChickenHouse.objects.create(farm=farm, name="Galpón A", area_m2=100)
        room = Room.objects.create(chicken_house=barn, name="Salón 1", area_m2=50)

        response = self.client.post(reverse("production:room-delete", args=[room.pk]))
        self.assertRedirects(response, reverse("production:infrastructure"))
        self.assertFalse(Room.objects.filter(pk=room.pk).exists())

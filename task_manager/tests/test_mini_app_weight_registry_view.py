from __future__ import annotations

import json
from datetime import timedelta

from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from personal.models import (
    CalendarStatus,
    PositionCategory,
    PositionCategoryCode,
    PositionDefinition,
    ShiftAssignment,
    ShiftCalendar,
    ShiftType,
    UserProfile,
)
from production.models import ChickenHouse, Farm, Room, WeightSampleSession


class MiniAppWeightRegistryViewTests(TestCase):
    def setUp(self):
        self.farm = Farm.objects.create(name="Granja Principal")
        self.chicken_house = ChickenHouse.objects.create(
            farm=self.farm,
            name="Galpón A",
            area_m2=250.0,
        )
        self.room = Room.objects.create(
            chicken_house=self.chicken_house,
            name="Sala 1",
            area_m2=120.0,
        )
        self.category = PositionCategory.objects.create(
            code=PositionCategoryCode.AUXILIAR_OPERATIVO,
            shift_type=ShiftType.DAY,
        )
        today = timezone.localdate()
        self.position = PositionDefinition.objects.create(
            name="Auxiliar Operativo",
            code="AUX-OP-02",
            category=self.category,
            farm=self.farm,
            chicken_house=self.chicken_house,
            valid_from=today - timedelta(days=30),
            valid_until=today + timedelta(days=30),
        )
        self.position.rooms.add(self.room)

    def _create_user(self, *, grant_permission: bool) -> UserProfile:
        user = UserProfile.objects.create_user(
            "200300400",
            password=None,
            nombres="Luis",
            apellidos="Martínez",
            telefono="3018884455",
        )
        access_perm = Permission.objects.get(codename="access_mini_app")
        user.user_permissions.add(access_perm)
        if grant_permission:
            weight_perm = Permission.objects.get(codename="view_mini_app_weight_registry_card")
            user.user_permissions.add(weight_perm)
        return user

    def _create_assignment(self, *, operator: UserProfile) -> ShiftAssignment:
        today = timezone.localdate()
        calendar = ShiftCalendar.objects.create(
            name="Calendario Pesaje",
            start_date=today - timedelta(days=1),
            end_date=today + timedelta(days=1),
            status=CalendarStatus.APPROVED,
        )
        return ShiftAssignment.objects.create(
            calendar=calendar,
            position=self.position,
            date=today,
            operator=operator,
        )

    def test_weight_registry_payload_present_for_authorized_user(self):
        user = self._create_user(grant_permission=True)
        self._create_assignment(operator=user)
        self.client.force_login(user)

        response = self.client.get(reverse("task_manager:telegram-mini-app"))
        self.assertEqual(response.status_code, 200)

        card_permissions = response.context["mini_app_card_permissions"]
        self.assertTrue(card_permissions["weight_registry"])

        payload = response.context["telegram_mini_app"]
        self.assertIsNotNone(payload)
        weight_payload = payload["weight_registry"]
        self.assertIsNotNone(weight_payload)
        self.assertEqual(
            weight_payload["submit_url"],
            reverse("task_manager:mini-app-weight-registry"),
        )
        locations = weight_payload["locations"]
        self.assertEqual(len(locations), 1)
        self.assertEqual(locations[0]["room_id"], self.room.pk)
        self.assertEqual(locations[0]["barn"], self.chicken_house.name)
        self.assertEqual(locations[0]["room"], self.room.name)

    def test_weight_registry_save_creates_session_and_samples(self):
        user = self._create_user(grant_permission=True)
        self._create_assignment(operator=user)
        self.client.force_login(user)

        url = reverse("task_manager:mini-app-weight-registry")
        today = timezone.localdate()
        payload = {
            "date": today.isoformat(),
            "sessions": [
                {
                    "id": f"room-{self.room.pk}",
                    "room_id": self.room.pk,
                    "entries": [1820.5, 1812.3, 1830.0],
                }
            ],
        }
        response = self.client.post(url, data=json.dumps(payload), content_type="application/json")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")

        session = WeightSampleSession.objects.get(room=self.room, date=today)
        self.assertEqual(session.sample_size, 3)
        self.assertEqual(session.created_by, user)
        self.assertEqual(session.updated_by, user)
        samples = list(session.samples.order_by("created_at"))
        self.assertEqual(len(samples), 3)
        self.assertAlmostEqual(float(samples[0].grams), 1820.5)
        self.assertAlmostEqual(float(samples[1].grams), 1812.3)

        self.assertIn("weight_registry", data)
        registry_payload = data["weight_registry"]
        self.assertIsNotNone(registry_payload)
        self.assertEqual(
            registry_payload["submit_url"],
            reverse("task_manager:mini-app-weight-registry"),
        )
        self.assertEqual(len(registry_payload["sessions"]), 1)
        session_payload = registry_payload["sessions"][0]
        self.assertEqual(session_payload["room_id"], self.room.pk)
        self.assertEqual(session_payload["metrics"]["count"], 3)

    def test_weight_registry_save_requires_permission(self):
        user = self._create_user(grant_permission=False)
        self._create_assignment(operator=user)
        self.client.force_login(user)

        url = reverse("task_manager:mini-app-weight-registry")
        payload = {
            "date": timezone.localdate().isoformat(),
            "sessions": [
                {
                    "id": f"room-{self.room.pk}",
                    "room_id": self.room.pk,
                    "entries": [1800],
                }
            ],
        }
        response = self.client.post(url, data=json.dumps(payload), content_type="application/json")
        self.assertEqual(response.status_code, 403)
        data = response.json()
        self.assertIn("error", data)

    def test_weight_registry_card_hidden_without_permission(self):
        user = self._create_user(grant_permission=False)
        self._create_assignment(operator=user)
        self.client.force_login(user)

        response = self.client.get(reverse("task_manager:telegram-mini-app"))
        self.assertEqual(response.status_code, 200)
        card_permissions = response.context["mini_app_card_permissions"]
        self.assertFalse(card_permissions["weight_registry"])

        payload = response.context["telegram_mini_app"]
        self.assertIsNotNone(payload)
        self.assertIsNone(payload["weight_registry"])

    def test_weight_registry_accepts_payload_with_empty_sessions(self):
        user = self._create_user(grant_permission=True)
        self._create_assignment(operator=user)
        extra_room = Room.objects.create(
            chicken_house=self.chicken_house,
            name="Sala 2",
            area_m2=95.0,
        )
        self.position.rooms.add(extra_room)
        self.client.force_login(user)

        url = reverse("task_manager:mini-app-weight-registry")
        today = timezone.localdate()
        payload = {
            "date": today.isoformat(),
            "sessions": [
                {
                    "id": f"room-{self.room.pk}",
                    "room_id": self.room.pk,
                    "entries": [1820.5, 1810.0],
                },
                {
                    "id": f"room-{extra_room.pk}",
                    "room_id": extra_room.pk,
                    "entries": [],
                },
            ],
        }

        response = self.client.post(url, data=json.dumps(payload), content_type="application/json")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")
        sessions = WeightSampleSession.objects.filter(date=today)
        self.assertEqual(sessions.count(), 1)
        session = sessions.first()
        assert session is not None
        self.assertEqual(session.room, self.room)
        self.assertEqual(session.sample_size, 2)

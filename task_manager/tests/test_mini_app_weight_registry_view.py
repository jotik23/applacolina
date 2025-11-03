from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal

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
from production.models import ChickenHouse, Farm, Room, WeightSample, WeightSampleSession
from task_manager.models import TaskAssignment, TaskCategory, TaskDefinition, TaskStatus


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
        self.task_status = TaskStatus.objects.create(name="Activa", is_active=True)
        self.task_category = TaskCategory.objects.create(name="Operaciones", is_active=True)

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

    def _create_weight_task(self, *, operator: UserProfile, due_date: date) -> TaskAssignment:
        definition = TaskDefinition.objects.create(
            name="Pesaje aves",
            description="Captura los pesos para la jornada",
            status=self.task_status,
            category=self.task_category,
            task_type=TaskDefinition.TaskType.ONE_TIME,
            scheduled_for=due_date,
            position=self.position,
            collaborator=operator,
            record_format=TaskDefinition.RecordFormat.BIRD_WEIGHT,
        )
        return TaskAssignment.objects.create(
            task_definition=definition,
            collaborator=operator,
            due_date=due_date,
        )

    def test_weight_registry_payload_present_for_authorized_user(self):
        user = self._create_user(grant_permission=True)
        today = timezone.localdate()
        self._create_assignment(operator=user)
        assignment = self._create_weight_task(operator=user, due_date=today)
        self.client.force_login(user)

        response = self.client.get(reverse("task_manager:telegram-mini-app"))
        self.assertEqual(response.status_code, 200)

        card_permissions = response.context["mini_app_card_permissions"]
        self.assertTrue(card_permissions["weight_registry"])

        payload = response.context["telegram_mini_app"]
        self.assertIsNotNone(payload)
        weight_payload = payload["weight_registry"]
        self.assertIsNotNone(weight_payload)
        self.assertEqual(weight_payload["task_assignment_id"], assignment.pk)
        self.assertEqual(weight_payload["task_definition_id"], assignment.task_definition_id)
        self.assertIsNone(weight_payload["production_record_id"])
        self.assertIn("context_token", weight_payload)
        self.assertTrue(weight_payload["context_token"])
        initial_context_token = weight_payload["context_token"]
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
        today = timezone.localdate()
        self._create_assignment(operator=user)
        assignment = self._create_weight_task(operator=user, due_date=today)
        self.client.force_login(user)

        url = reverse("task_manager:mini-app-weight-registry")
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
        self.assertEqual(session.task_assignment, assignment)
        samples = list(session.samples.order_by("created_at"))
        self.assertEqual(len(samples), 3)
        self.assertAlmostEqual(float(samples[0].grams), 1820.5)
        self.assertAlmostEqual(float(samples[1].grams), 1812.3)

        self.assertIn("weight_registry", data)
        registry_payload = data["weight_registry"]
        self.assertIsNotNone(registry_payload)
        self.assertEqual(registry_payload["task_assignment_id"], assignment.pk)
        self.assertIn("context_token", registry_payload)
        self.assertTrue(registry_payload["context_token"])
        self.assertEqual(registry_payload["context_token"], initial_context_token)
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
        today = timezone.localdate()
        self._create_assignment(operator=user)
        self._create_weight_task(operator=user, due_date=today)
        self.client.force_login(user)

        response = self.client.get(reverse("task_manager:telegram-mini-app"))
        self.assertEqual(response.status_code, 200)
        card_permissions = response.context["mini_app_card_permissions"]
        self.assertFalse(card_permissions["weight_registry"])

        payload = response.context["telegram_mini_app"]
        self.assertIsNotNone(payload)
        self.assertIsNone(payload["weight_registry"])

    def test_weight_registry_card_hidden_without_matching_task(self):
        user = self._create_user(grant_permission=True)
        today = timezone.localdate()
        self._create_assignment(operator=user)
        self._create_weight_task(operator=user, due_date=today + timedelta(days=1))
        self.client.force_login(user)

        response = self.client.get(reverse("task_manager:telegram-mini-app"))
        self.assertEqual(response.status_code, 200)
        card_permissions = response.context["mini_app_card_permissions"]
        self.assertTrue(card_permissions["weight_registry"])

        payload = response.context["telegram_mini_app"]
        self.assertIsNotNone(payload)
        self.assertIsNone(payload["weight_registry"])

    def test_weight_registry_card_visible_for_position_level_task(self):
        user = self._create_user(grant_permission=True)
        today = timezone.localdate()
        self._create_assignment(operator=user)
        definition = TaskDefinition.objects.create(
            name="Pesaje aves turno",
            description="Pesaje general para la posición",
            status=self.task_status,
            category=self.task_category,
            task_type=TaskDefinition.TaskType.ONE_TIME,
            scheduled_for=today,
            position=self.position,
            record_format=TaskDefinition.RecordFormat.BIRD_WEIGHT,
        )
        assignment = TaskAssignment.objects.create(
            task_definition=definition,
            collaborator=None,
            due_date=today,
        )
        self.client.force_login(user)

        response = self.client.get(reverse("task_manager:telegram-mini-app"))
        self.assertEqual(response.status_code, 200)
        card_permissions = response.context["mini_app_card_permissions"]
        self.assertTrue(card_permissions["weight_registry"])

        payload = response.context["telegram_mini_app"]
        self.assertIsNotNone(payload)
        weight_payload = payload["weight_registry"]
        self.assertIsNotNone(weight_payload)
        self.assertEqual(weight_payload["task_assignment_id"], assignment.pk)
        self.assertIn("context_token", weight_payload)
        self.assertTrue(weight_payload["context_token"])

    def test_weight_registry_accepts_payload_with_empty_sessions(self):
        user = self._create_user(grant_permission=True)
        today = timezone.localdate()
        self._create_assignment(operator=user)
        assignment = self._create_weight_task(operator=user, due_date=today)
        extra_room = Room.objects.create(
            chicken_house=self.chicken_house,
            name="Sala 2",
            area_m2=95.0,
        )
        self.position.rooms.add(extra_room)
        self.client.force_login(user)

        url = reverse("task_manager:mini-app-weight-registry")
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
        self.assertEqual(session.task_assignment, assignment)

    def test_weight_registry_context_token_changes_after_logout(self):
        user = self._create_user(grant_permission=True)
        today = timezone.localdate()
        self._create_assignment(operator=user)
        self._create_weight_task(operator=user, due_date=today)
        self.client.force_login(user)

        first_response = self.client.get(reverse("task_manager:telegram-mini-app"))
        self.assertEqual(first_response.status_code, 200)
        first_payload = first_response.context["telegram_mini_app"]
        assert first_payload is not None
        first_weight = first_payload["weight_registry"]
        assert first_weight is not None
        first_context_token = first_weight["context_token"]
        self.assertTrue(first_context_token)

        self.client.logout()

        self.client.force_login(user)
        second_response = self.client.get(reverse("task_manager:telegram-mini-app"))
        self.assertEqual(second_response.status_code, 200)
        second_payload = second_response.context["telegram_mini_app"]
        assert second_payload is not None
        second_weight = second_payload["weight_registry"]
        assert second_weight is not None
        second_context_token = second_weight["context_token"]
        self.assertTrue(second_context_token)
        self.assertNotEqual(first_context_token, second_context_token)

    def test_weight_registry_reassigns_legacy_session_to_task(self):
        user = self._create_user(grant_permission=True)
        today = timezone.localdate()
        self._create_assignment(operator=user)
        assignment = self._create_weight_task(operator=user, due_date=today)

        legacy_session = WeightSampleSession.objects.create(
            date=today,
            room=self.room,
            unit="g",
            tolerance_percent=10,
            minimum_sample=30,
            birds=1200,
            sample_size=1,
            average_grams=Decimal("1800.00"),
            variance_grams=Decimal("0.00"),
            min_grams=Decimal("1800.00"),
            max_grams=Decimal("1800.00"),
            uniformity_percent=Decimal("100.00"),
            within_tolerance=1,
            created_by=user,
            updated_by=user,
            submitted_at=timezone.now(),
        )
        WeightSample.objects.create(
            session=legacy_session,
            grams=Decimal("1800.00"),
            recorded_by=user,
        )

        self.client.force_login(user)

        response = self.client.get(reverse("task_manager:telegram-mini-app"))
        self.assertEqual(response.status_code, 200)
        payload = response.context["telegram_mini_app"]
        self.assertIsNotNone(payload)
        weight_payload = payload["weight_registry"]
        self.assertIsNotNone(weight_payload)
        self.assertEqual(weight_payload["task_assignment_id"], assignment.pk)
        self.assertEqual(len(weight_payload["sessions"]), 1)
        self.assertTrue(weight_payload["context_token"])

        url = reverse("task_manager:mini-app-weight-registry")
        update_payload = {
            "date": today.isoformat(),
            "sessions": [
                {
                    "id": f"room-{self.room.pk}",
                    "room_id": self.room.pk,
                    "entries": [1810.0, 1820.0],
                }
            ],
        }
        update_response = self.client.post(
            url,
            data=json.dumps(update_payload),
            content_type="application/json",
        )
        self.assertEqual(update_response.status_code, 200)
        legacy_session.refresh_from_db()
        self.assertEqual(legacy_session.task_assignment, assignment)
        self.assertEqual(legacy_session.sample_size, 2)
        response_payload = update_response.json()["weight_registry"]
        assert response_payload is not None
        self.assertTrue(response_payload["context_token"])

from __future__ import annotations

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
from production.models import (
    BirdBatch,
    BirdBatchRoomAllocation,
    ChickenHouse,
    Farm,
    ProductionRecord,
    Room,
)


class MiniAppProductionViewTests(TestCase):
    def setUp(self):
        self.farm = Farm.objects.create(name="Granja Principal")
        self.chicken_house = ChickenHouse.objects.create(
            farm=self.farm,
            name="Galpón A",
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
            code="AUX-OP-01",
            category=self.category,
            farm=self.farm,
            chicken_house=self.chicken_house,
            valid_from=today - timedelta(days=30),
            valid_until=today + timedelta(days=30),
        )
        self.position.rooms.add(self.room)

        self.bird_batch = BirdBatch.objects.create(
            farm=self.farm,
            status=BirdBatch.Status.ACTIVE,
            birth_date=today - timedelta(weeks=40),
            initial_quantity=1000,
            breed="Hy-Line Brown",
        )
        BirdBatchRoomAllocation.objects.create(
            bird_batch=self.bird_batch,
            room=self.room,
            quantity=960,
        )

    def _create_user(self, *, grant_permission: bool) -> UserProfile:
        user = UserProfile.objects.create_user(
            "100200300",
            password=None,
            nombres="Ana",
            apellidos="García",
            telefono="3015556677",
        )
        access_perm = Permission.objects.get(codename="access_mini_app")
        user.user_permissions.add(access_perm)
        if grant_permission:
            production_perm = Permission.objects.get(codename="view_mini_app_production_card")
            user.user_permissions.add(production_perm)
        return user

    def _create_assignment(self, *, operator: UserProfile) -> ShiftAssignment:
        today = timezone.localdate()
        calendar = ShiftCalendar.objects.create(
            name="Calendario Producción",
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

    def test_production_payload_present_for_authorized_user(self):
        user = self._create_user(grant_permission=True)
        self._create_assignment(operator=user)
        self.client.force_login(user)

        response = self.client.get(reverse("task_manager:telegram-mini-app"))
        self.assertEqual(response.status_code, 200)

        card_permissions = response.context["mini_app_card_permissions"]
        self.assertTrue(card_permissions["production"])

        payload = response.context["telegram_mini_app"]
        self.assertIsNotNone(payload)
        production_payload = payload["production"]
        self.assertIsNotNone(production_payload)

        assert production_payload  # appease mypy
        self.assertEqual(
            production_payload["submit_url"],
            reverse("task_manager:mini-app-production-records"),
        )
        lots = production_payload["lots"]
        self.assertEqual(len(lots), 1)
        lot_payload = lots[0]
        self.assertEqual(lot_payload["id"], self.bird_batch.pk)
        self.assertEqual(lot_payload["birds"], 960)
        self.assertIsNone(lot_payload["record"])

    def test_production_card_hidden_without_permission(self):
        user = self._create_user(grant_permission=False)
        self._create_assignment(operator=user)
        self.client.force_login(user)

        response = self.client.get(reverse("task_manager:telegram-mini-app"))
        self.assertEqual(response.status_code, 200)

        card_permissions = response.context["mini_app_card_permissions"]
        self.assertFalse(card_permissions["production"])

        payload = response.context["telegram_mini_app"]
        self.assertIsNotNone(payload)
        self.assertIsNone(payload["production"])
        self.assertEqual(payload["production_reference"]["active_hens"], 0)

    def test_production_record_creation_via_api(self):
        user = self._create_user(grant_permission=True)
        self._create_assignment(operator=user)
        self.client.force_login(user)

        url = reverse("task_manager:mini-app-production-records")
        today = timezone.localdate()
        payload = {
            "date": today.isoformat(),
            "lots": [
                {
                    "bird_batch": self.bird_batch.pk,
                    "production": "152.5",
                    "consumption": "480.2",
                    "mortality": 3,
                    "discard": 5,
                    "average_egg_weight": "10200",
                }
            ],
        }

        response = self.client.post(url, payload, content_type="application/json")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")
        record = ProductionRecord.objects.get(bird_batch=self.bird_batch, date=today)
        self.assertEqual(record.created_by, user)
        self.assertEqual(record.updated_by, user)
        self.assertAlmostEqual(float(record.production), 152.5)
        self.assertAlmostEqual(float(record.consumption), 480.2)
        self.assertEqual(record.mortality, 3)
        self.assertEqual(record.discard, 5)
        self.assertAlmostEqual(float(record.average_egg_weight), 10200.00)

    def test_production_record_update_preserves_created_by(self):
        user = self._create_user(grant_permission=True)
        other_user = self._create_user(grant_permission=True)
        self._create_assignment(operator=user)
        record = ProductionRecord.objects.create(
            bird_batch=self.bird_batch,
            date=timezone.localdate(),
            production=140,
            consumption=470,
            mortality=2,
            discard=4,
            average_egg_weight=61.0,
            created_by=other_user,
            updated_by=other_user,
        )
        self.client.force_login(user)

        url = reverse("task_manager:mini-app-production-records")
        payload = {
            "date": record.date.isoformat(),
            "lots": [
                {
                    "bird_batch": self.bird_batch.pk,
                    "production": "160",
                    "consumption": "500",
                    "mortality": 1,
                    "discard": 6,
                    "average_egg_weight": "63.5",
                }
            ],
        }
        response = self.client.post(url, payload, content_type="application/json")
        self.assertEqual(response.status_code, 200)
        record.refresh_from_db()
        self.assertEqual(record.created_by, other_user)
        self.assertEqual(record.updated_by, user)
        self.assertAlmostEqual(float(record.production), 160)
        self.assertEqual(record.mortality, 1)
        self.assertAlmostEqual(float(record.average_egg_weight), 63.5)

    def test_production_record_validation_error(self):
        user = self._create_user(grant_permission=True)
        self._create_assignment(operator=user)
        self.client.force_login(user)

        url = reverse("task_manager:mini-app-production-records")
        payload = {
            "date": timezone.localdate().isoformat(),
            "lots": [
                {
                    "bird_batch": self.bird_batch.pk,
                    "production": "",
                    "consumption": "500",
                    "mortality": 0,
                    "discard": 0,
                }
            ],
        }
        response = self.client.post(url, payload, content_type="application/json")
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("error", data)
        self.assertTrue(ProductionRecord.objects.filter(bird_batch=self.bird_batch).count() == 0)

    def test_average_weight_exceeds_allowed_digits_returns_validation_error(self):
        user = self._create_user(grant_permission=True)
        self._create_assignment(operator=user)
        self.client.force_login(user)

        url = reverse("task_manager:mini-app-production-records")
        payload = {
            "date": timezone.localdate().isoformat(),
            "lots": [
                {
                    "bird_batch": self.bird_batch.pk,
                    "production": "120",
                    "consumption": "450",
                    "mortality": 0,
                    "discard": 0,
                    "average_egg_weight": "100000000000",  # 11 digits before decimal
                }
            ],
        }
        response = self.client.post(url, payload, content_type="application/json")
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("error", data)
        self.assertFalse(ProductionRecord.objects.exists())

    def test_production_record_requires_permission(self):
        user = self._create_user(grant_permission=False)
        self._create_assignment(operator=user)
        self.client.force_login(user)
        url = reverse("task_manager:mini-app-production-records")
        payload = {
            "date": timezone.localdate().isoformat(),
            "lots": [
                {
                    "bird_batch": self.bird_batch.pk,
                    "production": "120",
                    "consumption": "450",
                    "mortality": 1,
                    "discard": 2,
                }
            ],
        }
        response = self.client.post(url, payload, content_type="application/json")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(ProductionRecord.objects.exists())

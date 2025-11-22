from __future__ import annotations

from datetime import timedelta
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
from production.models import (
    BirdBatch,
    BirdBatchRoomAllocation,
    BreedReference,
    BreedWeeklyGuide,
    ChickenHouse,
    Farm,
    ProductionRecord,
    ProductionRoomRecord,
    Room,
)


class MiniAppFeedPlanCardTests(TestCase):
    def setUp(self):
        self._sequence = 0
        self.farm = Farm.objects.create(name="Granja Principal")
        self.breed = BreedReference.objects.create(name="Hy-Line Brown")
        self.chicken_house = ChickenHouse.objects.create(farm=self.farm, name="Galpón 1")
        self.room = Room.objects.create(chicken_house=self.chicken_house, name="Sala 1", area_m2=120)
        self.category, _ = PositionCategory.objects.get_or_create(
            code=PositionCategoryCode.OFICIOS_VARIOS,
            defaults={"shift_type": ShiftType.DAY},
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
            initial_quantity=1200,
            breed=self.breed,
        )
        BirdBatchRoomAllocation.objects.create(
            bird_batch=self.bird_batch,
            room=self.room,
            quantity=1000,
        )
        BreedWeeklyGuide.objects.create(
            breed=self.breed,
            week=40,
            grams_per_bird=Decimal("110.0"),
        )

    def _next_identifier(self) -> str:
        self._sequence += 1
        return f"{self._sequence:04d}"

    def _create_user(self, *, grant_permission: bool) -> UserProfile:
        identifier = self._next_identifier()
        user = UserProfile.objects.create_user(
            f"1002{identifier}00",
            password=None,
            nombres="Test",
            apellidos="Operario",
            telefono=f"301{identifier}00",
        )
        access_perm = Permission.objects.get(codename="access_mini_app")
        user.user_permissions.add(access_perm)
        if grant_permission:
            feed_perm = Permission.objects.get(codename="view_mini_app_feed_card")
            user.user_permissions.add(feed_perm)
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

    def _register_room_mortality(self, *, quantity: int = 10) -> None:
        today = timezone.localdate()
        record = ProductionRecord.objects.create(
            bird_batch=self.bird_batch,
            date=today,
            production=Decimal("0"),
            consumption=Decimal("0"),
            mortality=quantity,
            discard=0,
        )
        ProductionRoomRecord.objects.create(
            production_record=record,
            room=self.room,
            production=Decimal("0"),
            consumption=Decimal("0"),
            mortality=quantity,
            discard=0,
        )

    def test_feed_plan_hidden_without_permission(self):
        user = self._create_user(grant_permission=False)
        self._create_assignment(operator=user)
        self._register_room_mortality()
        self.client.force_login(user)

        response = self.client.get(reverse("task_manager:telegram-mini-app"))
        self.assertEqual(response.status_code, 200)

        card_permissions = response.context["mini_app_card_permissions"]
        self.assertFalse(card_permissions["feed_plan"])

        payload = response.context["telegram_mini_app"]
        self.assertIsNotNone(payload)
        self.assertIsNone(payload["feed_plan"])

    def test_feed_plan_payload_shows_expected_distribution(self):
        user = self._create_user(grant_permission=True)
        self._create_assignment(operator=user)
        self._register_room_mortality(quantity=10)
        self.client.force_login(user)

        response = self.client.get(reverse("task_manager:telegram-mini-app"))
        self.assertEqual(response.status_code, 200)

        payload = response.context["telegram_mini_app"]
        self.assertIsNotNone(payload)
        feed_plan = payload["feed_plan"]
        self.assertIsNotNone(feed_plan)

        assert feed_plan  # appease type checkers
        self.assertEqual(feed_plan["houses"][0]["rooms"][0]["birds"], 990)
        self.assertAlmostEqual(feed_plan["totals"]["feed_kg"], 108.9, places=1)
        self.assertAlmostEqual(feed_plan["totals"]["feed_bags"], 2.72, places=2)

        recommended = feed_plan["distribution"]["recommended"]
        self.assertIsNotNone(recommended)
        assert recommended
        self.assertEqual(recommended["total_bags"], 3)
        self.assertEqual(recommended["morning_bags"], 2)
        self.assertEqual(recommended["afternoon_bags"], 1)

        reference = feed_plan["reference"]
        self.assertAlmostEqual(reference["grams_per_bird"], 110.0, places=1)
        self.assertAlmostEqual(reference["rounded_grams_per_bird"], 121.21, places=2)

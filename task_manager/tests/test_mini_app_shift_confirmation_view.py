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
from production.models import ChickenHouse, Farm, Room


class MiniAppShiftConfirmationViewTests(TestCase):
    def setUp(self):
        self.farm = Farm.objects.create(name="Granja Integración")
        self.chicken_house = ChickenHouse.objects.create(
            farm=self.farm,
            name="Galpón Uno",
            area_m2=280.0,
        )
        self.room = Room.objects.create(
            chicken_house=self.chicken_house,
            name="Sala Principal",
            area_m2=140.0,
        )
        self.category = PositionCategory.objects.create(
            code=PositionCategoryCode.LIDER_GRANJA,
            shift_type=ShiftType.DAY,
        )
        self.position = PositionDefinition.objects.create(
            name="Líder Operativo",
            code="LID-OP-001",
            category=self.category,
            farm=self.farm,
            chicken_house=self.chicken_house,
            valid_from=timezone.localdate() - timedelta(days=30),
            valid_until=timezone.localdate() + timedelta(days=30),
        )
        self.position.rooms.add(self.room)

    def _create_user(self, *, grant_shift_permission: bool) -> UserProfile:
        user = UserProfile.objects.create_user(
            "900900900",
            password=None,
            nombres="Julio",
            apellidos="Mejía",
            telefono="3005006000",
        )
        access_perm = Permission.objects.get(codename="access_mini_app")
        shift_perm = Permission.objects.get(codename="view_mini_app_shift_confirmation_card")
        user.user_permissions.add(access_perm)
        if grant_shift_permission:
            user.user_permissions.add(shift_perm)
        return user

    def _create_assignment(self, *, operator: UserProfile):
        today = timezone.localdate()
        calendar = ShiftCalendar.objects.create(
            name="Calendario Integración",
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

    def test_authorized_user_sees_shift_card(self):
        user = self._create_user(grant_shift_permission=True)
        self._create_assignment(operator=user)
        self.client.force_login(user)

        response = self.client.get(reverse("task_manager:telegram-mini-app"))
        self.assertEqual(response.status_code, 200)

        card_permissions = response.context["mini_app_card_permissions"]
        self.assertTrue(card_permissions["shift_confirmation"])

        payload = response.context["telegram_mini_app"]
        self.assertIsNotNone(payload)
        shift_payload = payload["shift_confirmation"]
        self.assertIsNotNone(shift_payload)
        self.assertIsNone(payload["shift_confirmation_empty"])
        self.assertIn("greeting_label", shift_payload)
        self.assertIn(user.get_short_name(), shift_payload["greeting_label"])
        expected_summary = f"{self.category.display_name} · {self.position.name}"
        self.assertEqual(shift_payload["summary_label"], expected_summary)
        self.assertContains(response, "data-shift-confirmation-card")
        self.assertContains(response, expected_summary)

    def test_authorized_user_without_assignment_gets_empty_card(self):
        user = self._create_user(grant_shift_permission=True)
        self.client.force_login(user)

        response = self.client.get(reverse("task_manager:telegram-mini-app"))
        self.assertEqual(response.status_code, 200)

        payload = response.context["telegram_mini_app"]
        self.assertIsNotNone(payload)
        shift_payload = payload["shift_confirmation"]
        self.assertIsNone(shift_payload)
        empty_payload = payload["shift_confirmation_empty"]
        self.assertIsNotNone(empty_payload)
        assert empty_payload  # type checkers
        self.assertIn("turno", empty_payload["headline"].lower())
        self.assertContains(response, "La mini app se desbloqueará")

    def test_shift_card_hidden_without_specific_permission(self):
        user = self._create_user(grant_shift_permission=False)
        self._create_assignment(operator=user)
        self.client.force_login(user)

        response = self.client.get(reverse("task_manager:telegram-mini-app"))
        self.assertEqual(response.status_code, 200)

        card_permissions = response.context["mini_app_card_permissions"]
        self.assertFalse(card_permissions["shift_confirmation"])

        payload = response.context["telegram_mini_app"]
        self.assertIsNotNone(payload)
        self.assertIsNone(payload["shift_confirmation"])
        self.assertIsNone(payload["shift_confirmation_empty"])
        self.assertNotContains(response, "data-shift-confirmation-card")


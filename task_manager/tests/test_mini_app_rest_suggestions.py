from __future__ import annotations

import json
from datetime import date, timedelta

from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from personal.models import (
    CalendarRestSuggestion,
    CalendarStatus,
    OperatorRestPeriod,
    RestPeriodSource,
    RestPeriodStatus,
    ShiftCalendar,
    UserProfile,
)


class MiniAppRestSuggestionViewTests(TestCase):
    def setUp(self) -> None:
        self.user = UserProfile.objects.create_user(
            cedula="900100100",
            password="test",  # noqa: S106 - test credential
            nombres="Operario",
            apellidos="Descanso",
        )
        self.access_perm = Permission.objects.get(codename="access_mini_app")
        self.user.user_permissions.add(self.access_perm)
        self.client.force_login(self.user)

        self.calendar = ShiftCalendar.objects.create(
            name="Semana 50",
            start_date=date(2025, 12, 1),
            end_date=date(2025, 12, 7),
            status=CalendarStatus.DRAFT,
            created_by=self.user,
        )
        self.rest_period = OperatorRestPeriod.objects.create(
            operator=self.user,
            start_date=self.calendar.start_date,
            end_date=self.calendar.start_date,
            status=RestPeriodStatus.APPROVED,
            source=RestPeriodSource.CALENDAR,
            calendar=self.calendar,
        )

    def test_register_suggestion(self) -> None:
        url = reverse("task_manager:mini-app-rest-suggestions")
        payload = {
            "scheduled_date": self.rest_period.start_date.isoformat(),
            "suggested_date": (self.rest_period.start_date + timedelta(days=2)).isoformat(),
            "reason": "Necesito ajustar la visita médica familiar.",
            "calendar_id": self.calendar.id,
        }

        response = self.client.post(url, data=json.dumps(payload), content_type="application/json")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(CalendarRestSuggestion.objects.count(), 1)
        suggestion = CalendarRestSuggestion.objects.get()
        self.assertEqual(suggestion.operator, self.user)
        self.assertEqual(suggestion.calendar, self.calendar)
        self.assertEqual(suggestion.scheduled_date, self.rest_period.start_date)

    def test_requires_matching_rest_period(self) -> None:
        self.rest_period.delete()
        url = reverse("task_manager:mini-app-rest-suggestions")
        payload = {
            "scheduled_date": self.calendar.start_date.isoformat(),
            "suggested_date": self.calendar.start_date.isoformat(),
            "reason": "Cambio por favor",
            "calendar_id": self.calendar.id,
        }

        response = self.client.post(url, data=json.dumps(payload), content_type="application/json")

        self.assertEqual(response.status_code, 400)
        self.assertIn("errors", response.json())

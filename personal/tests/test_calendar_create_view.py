from __future__ import annotations

from datetime import date

from django.test import TestCase
from django.urls import reverse

from personal.models import CalendarStatus, ShiftCalendar
from personal.models import UserProfile


class CalendarCreateViewTests(TestCase):
    def setUp(self) -> None:
        self.user = UserProfile.objects.create_user(
            cedula="9010",
            password="test",  # noqa: S106 - test credential
            nombres="Claudia",
            apellidos="Ramírez",
            telefono="3000000010",
            is_staff=True,
        )
        self.client.force_login(self.user)
        self.url = reverse("personal:calendar-create")

    def test_creates_calendar_and_returns_redirect_url(self) -> None:
        response = self.client.post(
            self.url,
            data={
                "name": "Semana operativa",
                "start_date": date(2025, 1, 6),
                "end_date": date(2025, 1, 12),
                "notes": "Incluye jornada extendida",
            },
        )

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertTrue(payload.get("success"))
        redirect_url = payload.get("redirect_url")
        self.assertIsInstance(redirect_url, str)
        self.assertIn("/calendars/", redirect_url or "")

        self.assertEqual(ShiftCalendar.objects.count(), 1)
        calendar = ShiftCalendar.objects.get()
        self.assertEqual(calendar.name, "Semana operativa")
        self.assertEqual(calendar.start_date, date(2025, 1, 6))
        self.assertEqual(calendar.end_date, date(2025, 1, 12))
        self.assertEqual(calendar.notes, "Incluye jornada extendida")
        self.assertEqual(calendar.created_by, self.user)
        self.assertEqual(calendar.status, CalendarStatus.DRAFT)

    def test_invalid_dates_return_validation_errors(self) -> None:
        response = self.client.post(
            self.url,
            data={
                "name": "Rango inválido",
                "start_date": date(2025, 2, 10),
                "end_date": date(2025, 2, 4),
            },
        )

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertFalse(payload.get("success"))
        errors = payload.get("errors", {})
        self.assertIn("__all__", errors)
        self.assertTrue(any("fecha" in error.lower() for error in errors["__all__"]))
        self.assertEqual(ShiftCalendar.objects.count(), 0)

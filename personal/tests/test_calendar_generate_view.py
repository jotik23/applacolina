from __future__ import annotations

import json

from django.test import TestCase
from django.urls import reverse

from personal.models import CalendarStatus, ShiftCalendar
from personal.models import UserProfile


class CalendarGenerateViewTests(TestCase):
    def setUp(self) -> None:
        self.user = UserProfile.objects.create_user(
            cedula="9001",
            password="test",  # noqa: S106 - test credential
            nombres="Test",
            apellidos="User",
            telefono="3000000001",
            is_staff=True,
        )
        self.client.force_login(self.user)
        self.url = reverse("personal-api:calendar-generate")

    def test_reuses_existing_draft_calendar_for_same_range(self) -> None:
        payload = {
            "name": "Calendario inicial",
            "start_date": "2025-10-27",
            "end_date": "2025-11-02",
            "notes": "Primera propuesta",
        }

        response = self.client.post(self.url, data=json.dumps(payload), content_type="application/json")
        self.assertEqual(response.status_code, 201)

        body = response.json()
        calendar_id = body["calendar_id"]
        self.assertEqual(ShiftCalendar.objects.count(), 1)

        calendar = ShiftCalendar.objects.get(pk=calendar_id)
        self.assertEqual(calendar.name, payload["name"])
        self.assertEqual(calendar.notes, payload["notes"])
        self.assertEqual(calendar.created_by, self.user)
        self.assertEqual(calendar.status, CalendarStatus.DRAFT)

        second_payload = {
            "name": "Calendario actualizado",
            "start_date": payload["start_date"],
            "end_date": payload["end_date"],
            "notes": "Nueva propuesta",
        }

        second_response = self.client.post(
            self.url,
            data=json.dumps(second_payload),
            content_type="application/json",
        )
        self.assertEqual(second_response.status_code, 201)
        self.assertEqual(ShiftCalendar.objects.count(), 1)

        second_body = second_response.json()
        self.assertEqual(second_body["calendar_id"], calendar_id)

        calendar.refresh_from_db()
        self.assertEqual(calendar.name, second_payload["name"])
        self.assertEqual(calendar.notes, second_payload["notes"])
        self.assertEqual(calendar.created_by, self.user)
        self.assertEqual(calendar.status, CalendarStatus.DRAFT)

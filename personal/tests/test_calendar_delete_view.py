from __future__ import annotations

from datetime import date, timedelta

from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from personal.models import (
    CalendarStatus,
    OperatorRestPeriod,
    RestPeriodSource,
    RestPeriodStatus,
    ShiftCalendar,
)
from personal.models import UserProfile


class CalendarDeleteViewTests(TestCase):
    def setUp(self) -> None:
        self.user = UserProfile.objects.create_user(
            cedula="9002",
            password="test",  # noqa: S106 - test credential
            nombres="Elena",
            apellidos="Gomez",
            telefono="3000000002",
        )
        self.client.force_login(self.user)

    def test_delete_calendar_succeeds_and_redirects_to_next(self) -> None:
        calendar = ShiftCalendar.objects.create(
            name="Semana Operativa",
            start_date=date(2025, 6, 2),
            end_date=date(2025, 6, 8),
            status=CalendarStatus.DRAFT,
            created_by=self.user,
        )

        next_url = reverse("personal:configurator")
        response = self.client.post(
            reverse("personal:calendar-delete", args=[calendar.pk]),
            data={"next": next_url},
        )

        self.assertRedirects(response, next_url)
        self.assertFalse(ShiftCalendar.objects.filter(pk=calendar.pk).exists())

        messages = list(get_messages(response.wsgi_request))
        self.assertTrue(any("Se eliminó el calendario" in message.message for message in messages))

    def test_delete_calendar_blocked_when_it_has_modifications(self) -> None:
        base_calendar = ShiftCalendar.objects.create(
            name="Semana Aprobada",
            start_date=date(2025, 7, 1),
            end_date=date(2025, 7, 7),
            status=CalendarStatus.APPROVED,
            created_by=self.user,
        )
        ShiftCalendar.objects.create(
            name="Ajuste semana",
            start_date=base_calendar.start_date,
            end_date=base_calendar.end_date,
            status=CalendarStatus.MODIFIED,
            base_calendar=base_calendar,
            created_by=self.user,
        )

        response = self.client.post(reverse("personal:calendar-delete", args=[base_calendar.pk]))

        self.assertRedirects(response, reverse("personal:calendar-detail", args=[base_calendar.pk]))
        self.assertTrue(ShiftCalendar.objects.filter(pk=base_calendar.pk).exists())

        messages = list(get_messages(response.wsgi_request))
        self.assertTrue(any("No es posible eliminar este calendario" in message.message for message in messages))

    def test_delete_calendar_cleans_related_rest_periods(self) -> None:
        calendar = ShiftCalendar.objects.create(
            name="Semana Descansos",
            start_date=date(2025, 8, 4),
            end_date=date(2025, 8, 10),
            status=CalendarStatus.DRAFT,
            created_by=self.user,
        )

        operator = UserProfile.objects.create_user(
            cedula="9150",
            password="test",  # noqa: S106 - test credential
            nombres="Operario",
            apellidos="Descansos",
            telefono="3000000090",
        )

        period_calendar = OperatorRestPeriod.objects.create(
            operator=operator,
            start_date=calendar.start_date,
            end_date=calendar.start_date,
            status=RestPeriodStatus.CONFIRMED,
            source=RestPeriodSource.CALENDAR,
            calendar=calendar,
        )
        period_manual = OperatorRestPeriod.objects.create(
            operator=operator,
            start_date=calendar.start_date + timedelta(days=1),
            end_date=calendar.start_date + timedelta(days=2),
            status=RestPeriodStatus.CONFIRMED,
            source=RestPeriodSource.MANUAL,
            calendar=calendar,
        )

        response = self.client.post(reverse("personal:calendar-delete", args=[calendar.pk]))

        self.assertRedirects(response, reverse("personal:configurator"))
        self.assertFalse(ShiftCalendar.objects.filter(pk=calendar.pk).exists())
        self.assertFalse(OperatorRestPeriod.objects.filter(pk=period_calendar.pk).exists())

        period_manual.refresh_from_db()
        self.assertEqual(period_manual.status, RestPeriodStatus.APPROVED)
        self.assertIsNone(period_manual.calendar)

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from personal.models import (
    CalendarStatus,
    PositionCategory,
    PositionCategoryCode,
    PositionDefinition,
    ShiftCalendar,
    ShiftType,
    UserProfile,
)
from production.models import Farm


class CalendarDetailPDFViewTests(TestCase):
    def setUp(self) -> None:
        self.user = UserProfile.objects.create_user(
            cedula="1500",
            password="test",  # noqa: S106 - test credential
            nombres="Coordinador",
            apellidos="PDF",
            telefono="3100000000",
            is_staff=True,
        )
        self.client.force_login(self.user)

        self.farm = Farm.objects.create(name="Granja Centro")
        self.category, _ = PositionCategory.objects.get_or_create(
            code=PositionCategoryCode.GALPONERO_PRODUCCION_DIA,
            defaults={
                "shift_type": ShiftType.DAY,
                "rest_max_consecutive_days": 8,
                "rest_post_shift_days": 0,
                "rest_monthly_days": 4,
            },
        )

        self.calendar = ShiftCalendar.objects.create(
            name="Semana 1",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 7),
            status=CalendarStatus.DRAFT,
            created_by=self.user,
        )

        PositionDefinition.objects.create(
            name="Operador 1",
            code="OP-1",
            category=self.category,
            farm=self.farm,
            valid_from=self.calendar.start_date,
            valid_until=self.calendar.end_date,
        )

        self.url = reverse("personal:calendar-detail-pdf", args=[self.calendar.pk])

    @patch("personal.views._render_calendar_pdf", return_value=b"%PDF-TEST%")
    def test_generates_pdf_for_custom_range(self, render_pdf_mock) -> None:
        response = self.client.get(
            self.url,
            {
                "start_date": "2025-01-02",
                "end_date": "2025-01-05",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("20250102-20250105", response["Content-Disposition"])
        render_pdf_mock.assert_called_once()

    @patch("personal.views._render_calendar_pdf", return_value=b"%PDF-TEST%")
    def test_rejects_out_of_bounds_range(self, render_pdf_mock) -> None:
        response = self.client.get(
            self.url,
            {
                "start_date": "2024-12-30",
                "end_date": "2025-01-02",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("calendario seleccionado", response.content.decode())
        render_pdf_mock.assert_not_called()

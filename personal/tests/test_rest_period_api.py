from __future__ import annotations

import json
from datetime import date

from django.test import TestCase
from django.urls import reverse

from personal.models import (
    AssignmentAlertLevel,
    CalendarStatus,
    OperatorRestPeriod,
    PositionCategory,
    PositionCategoryCode,
    PositionDefinition,
    RestPeriodSource,
    RestPeriodStatus,
    ShiftAssignment,
    ShiftCalendar,
    ShiftType,
)
from production.models import Farm
from personal.models import UserProfile


class RestPeriodApiTests(TestCase):
    def setUp(self) -> None:
        self.admin_user = UserProfile.objects.create_user(
            cedula="9001",
            password="test",  # noqa: S106 - test credential
            nombres="Coordinador",
            apellidos="Principal",
            telefono="3000000000",
            is_staff=True,
        )
        self.operator = UserProfile.objects.create_user(
            cedula="8002",
            password="test",  # noqa: S106 - test credential
            nombres="Operario",
            apellidos="De Turno",
            telefono="3000000003",
        )
        self.client.force_login(self.admin_user)

        self.farm = Farm.objects.create(name="Colina API")
        self.category, _created = PositionCategory.objects.get_or_create(
            code=PositionCategoryCode.GALPONERO_PRODUCCION_DIA,
            defaults={
                "shift_type": ShiftType.DAY,
                "rest_max_consecutive_days": 8,
                "rest_post_shift_days": 0,
                "rest_monthly_days": 5,
                "is_active": True,
            },
        )
        if not _created:
            self.category.shift_type = ShiftType.DAY
            self.category.rest_max_consecutive_days = 8
            self.category.rest_post_shift_days = 0
            self.category.rest_monthly_days = 5
            self.category.is_active = True
            self.category.save()
        self.position = PositionDefinition.objects.create(
            name="Posición API",
            code="POS-API",
            category=self.category,
            farm=self.farm,
            valid_from=date(2025, 1, 1),
        )
        self.calendar = ShiftCalendar.objects.create(
            name="Semana API",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 15),
            status=CalendarStatus.DRAFT,
            created_by=self.admin_user,
        )
        self.assignment = ShiftAssignment.objects.create(
            calendar=self.calendar,
            position=self.position,
            date=date(2025, 1, 5),
            operator=self.operator,
            is_auto_assigned=False,
            alert_level=AssignmentAlertLevel.NONE,
            is_overtime=False,
        )
        self.rest_create_url = reverse("personal-api:calendar-rest-periods")

    def test_create_rest_period_flags_calendar_refresh_when_overlapping_assignment(self) -> None:
        payload = {
            "operator": self.operator.id,
            "start_date": "2025-01-05",
            "end_date": "2025-01-07",
            "status": RestPeriodStatus.CONFIRMED,
            "source": RestPeriodSource.MANUAL,
            "notes": "Descanso tras jornada extendida",
        }
        response = self.client.post(
            self.rest_create_url,
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertIn("rest_period", body)
        self.assertTrue(body.get("requires_calendar_refresh"))

        period = OperatorRestPeriod.objects.get(operator=self.operator, start_date=date(2025, 1, 5))
        self.assertEqual(period.end_date, date(2025, 1, 7))
        self.assertEqual(period.status, RestPeriodStatus.CONFIRMED)
        self.assertEqual(period.source, RestPeriodSource.MANUAL)
        self.assertEqual(period.created_by, self.admin_user)

    def test_update_rest_period_without_conflict_does_not_require_refresh(self) -> None:
        period = OperatorRestPeriod.objects.create(
            operator=self.operator,
            start_date=date(2025, 1, 9),
            end_date=date(2025, 1, 10),
            status=RestPeriodStatus.PLANNED,
            source=RestPeriodSource.MANUAL,
            created_by=self.admin_user,
        )
        detail_url = reverse("personal-api:calendar-rest-period-detail", args=[period.id])
        payload = {
            "start_date": "2025-01-12",
            "end_date": "2025-01-13",
            "status": RestPeriodStatus.APPROVED,
            "notes": "Ajuste por cambio de turno",
        }
        response = self.client.patch(
            detail_url,
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("rest_period", data)
        self.assertFalse(data.get("requires_calendar_refresh"))

        period.refresh_from_db()
        self.assertEqual(period.start_date, date(2025, 1, 12))
        self.assertEqual(period.status, RestPeriodStatus.APPROVED)
        self.assertEqual(period.notes, "Ajuste por cambio de turno")

    def test_delete_rest_period_generated_from_calendar_requests_refresh(self) -> None:
        period = OperatorRestPeriod.objects.create(
            operator=self.operator,
            start_date=date(2025, 1, 3),
            end_date=date(2025, 1, 4),
            status=RestPeriodStatus.APPROVED,
            source=RestPeriodSource.CALENDAR,
            calendar=self.calendar,
            created_by=self.admin_user,
        )
        detail_url = reverse("personal-api:calendar-rest-period-detail", args=[period.id])
        response = self.client.delete(detail_url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data.get("status"), "deleted")
        self.assertTrue(data.get("requires_calendar_refresh"))
        self.assertFalse(OperatorRestPeriod.objects.filter(pk=period.id).exists())

    def test_metadata_includes_rest_periods_and_choice_sets(self) -> None:
        OperatorRestPeriod.objects.create(
            operator=self.operator,
            start_date=date(2025, 1, 2),
            end_date=date(2025, 1, 3),
            status=RestPeriodStatus.PLANNED,
            source=RestPeriodSource.MANUAL,
            created_by=self.admin_user,
        )
        metadata_url = reverse("personal-api:calendar-metadata")
        response = self.client.get(metadata_url)
        self.assertEqual(response.status_code, 200)
        data = response.json()

        rest_periods = data.get("rest_periods", [])
        self.assertGreaterEqual(len(rest_periods), 1)
        sample = next((item for item in rest_periods if item.get("operator_id") == self.operator.id), None)
        self.assertIsNotNone(sample)
        self.assertIn("status_label", sample)
        self.assertIn("source_label", sample)
        self.assertIn("operator_id", sample)
        self.assertIn("duration_days", sample)

        choice_sets = data.get("choice_sets", {})
        self.assertIn("rest_statuses", choice_sets)
        self.assertIn("rest_sources", choice_sets)
        self.assertTrue(choice_sets["rest_statuses"])
        self.assertTrue(choice_sets["rest_sources"])

    def test_metadata_includes_operator_suggested_positions(self) -> None:
        self.operator.suggested_positions.add(self.position)

        metadata_url = reverse("personal-api:calendar-metadata")
        response = self.client.get(metadata_url)
        self.assertEqual(response.status_code, 200)
        data = response.json()

        operators = data.get("operators", [])
        operator_entry = next((item for item in operators if item.get("id") == self.operator.id), None)
        self.assertIsNotNone(operator_entry)

        suggestions = operator_entry.get("suggested_positions")
        self.assertIsInstance(suggestions, list)
        self.assertTrue(any(item.get("id") == self.position.id for item in suggestions))

    def test_metadata_includes_vacunador_category(self) -> None:
        category, _created = PositionCategory.objects.get_or_create(
            code=PositionCategoryCode.VACUNADOR,
            defaults={
                "shift_type": ShiftType.DAY,
                "rest_max_consecutive_days": 8,
                "rest_post_shift_days": 0,
                "rest_monthly_days": 5,
                "is_active": True,
            },
        )
        if not _created:
            update_fields: list[str] = []
            if category.shift_type != ShiftType.DAY:
                category.shift_type = ShiftType.DAY
                update_fields.append("shift_type")
            if category.rest_max_consecutive_days != 8:
                category.rest_max_consecutive_days = 8
                update_fields.append("rest_max_consecutive_days")
            if category.rest_post_shift_days != 0:
                category.rest_post_shift_days = 0
                update_fields.append("rest_post_shift_days")
            if category.rest_monthly_days != 5:
                category.rest_monthly_days = 5
                update_fields.append("rest_monthly_days")
            if not category.is_active:
                category.is_active = True
                update_fields.append("is_active")
            if update_fields:
                category.save(update_fields=update_fields)

        metadata_url = reverse("personal-api:calendar-metadata")
        response = self.client.get(metadata_url)
        self.assertEqual(response.status_code, 200)
        data = response.json()

        choice_sets = data.get("choice_sets", {})
        categories = choice_sets.get("position_categories", [])
        self.assertTrue(any(item.get("code") == PositionCategoryCode.VACUNADOR for item in categories))
        matching = next(item for item in categories if item.get("code") == PositionCategoryCode.VACUNADOR)
        self.assertEqual(matching.get("label"), category.display_name)
        self.assertEqual(int(matching.get("value")), category.id)

from __future__ import annotations

from datetime import date, timedelta

from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from calendario.models import (
    AssignmentAlertLevel,
    CalendarStatus,
    ComplexityLevel,
    OperatorCapability,
    PositionCategory,
    PositionCategoryCode,
    PositionDefinition,
    ShiftAssignment,
    ShiftCalendar,
    ShiftType,
)
from granjas.models import Farm
from users.models import UserProfile


class CalendarDetailViewManualOverrideTests(TestCase):
    def setUp(self) -> None:
        self.user = UserProfile.objects.create_user(
            cedula="1000",
            password="test",  # noqa: S106 - test credential
            nombres="Coordinador",
            apellidos="Calendario",
            telefono="3100000000",
        )
        self.client.force_login(self.user)

        self.farm = Farm.objects.create(name="Colina")

        self.category, _created = PositionCategory.objects.get_or_create(
            code=PositionCategoryCode.GALPONERO_PRODUCCION_DIA,
            defaults={
                "name": "Galponero producción día",
                "shift_type": ShiftType.DAY,
                "extra_day_limit": 3,
                "overtime_points": 1,
                "overload_alert_level": AssignmentAlertLevel.WARN,
                "rest_min_frequency": 6,
                "rest_min_consecutive_days": 5,
                "rest_max_consecutive_days": 8,
                "rest_post_shift_days": 0,
                "rest_monthly_days": 5,
            },
        )

        self.category.rest_min_frequency = 6
        self.category.rest_min_consecutive_days = 5
        self.category.rest_max_consecutive_days = 8
        self.category.rest_post_shift_days = 0
        self.category.rest_monthly_days = 5
        self.category.extra_day_limit = 3
        self.category.overtime_points = 1
        self.category.overload_alert_level = AssignmentAlertLevel.WARN
        self.category.save()

        self.calendar = ShiftCalendar.objects.create(
            name="Semana 27",
            start_date=date(2025, 7, 7),
            end_date=date(2025, 7, 13),
            status=CalendarStatus.DRAFT,
            created_by=self.user,
        )

        self.position_primary = PositionDefinition.objects.create(
            name="Galponero día A",
            code="POS-A",
            category=self.category,
            farm=self.farm,
            complexity=ComplexityLevel.BASIC,
            allow_lower_complexity=False,
            valid_from=self.calendar.start_date,
            valid_until=self.calendar.end_date,
        )
        self.position_secondary = PositionDefinition.objects.create(
            name="Galponero día B",
            code="POS-B",
            category=self.category,
            farm=self.farm,
            complexity=ComplexityLevel.BASIC,
            allow_lower_complexity=False,
            valid_from=self.calendar.start_date,
            valid_until=self.calendar.end_date,
        )

        self.operator_initial = UserProfile.objects.create_user(
            cedula="2000",
            password="test",  # noqa: S106 - test credential
            nombres="Operario",
            apellidos="Inicial",
            telefono="3100000001",
        )
        self.operator_conflict = UserProfile.objects.create_user(
            cedula="2001",
            password="test",  # noqa: S106 - test credential
            nombres="Operario",
            apellidos="Conflicto",
            telefono="3100000002",
        )
        self.operator_manual = UserProfile.objects.create_user(
            cedula="2002",
            password="test",  # noqa: S106 - test credential
            nombres="Operario",
            apellidos="Manual",
            telefono="3100000003",
        )

        OperatorCapability.objects.create(
            operator=self.operator_initial,
            category=self.position_primary.category,
            skill_score=5,
        )
        OperatorCapability.objects.create(
            operator=self.operator_conflict,
            category=self.position_primary.category,
            skill_score=5,
        )

        self.assignment_primary = ShiftAssignment.objects.create(
            calendar=self.calendar,
            position=self.position_primary,
            date=self.calendar.start_date,
            operator=self.operator_initial,
            alert_level=AssignmentAlertLevel.NONE,
            is_auto_assigned=True,
        )
        self.assignment_conflict = ShiftAssignment.objects.create(
            calendar=self.calendar,
            position=self.position_secondary,
            date=self.calendar.start_date,
            operator=self.operator_conflict,
            alert_level=AssignmentAlertLevel.NONE,
            is_auto_assigned=True,
        )

    def test_update_assignment_accepts_manual_override_with_conflict(self) -> None:
        url = reverse("calendario:calendar-detail", args=[self.calendar.pk])
        response = self.client.post(
            url,
            data={
                "action": "update-assignment",
                "assignment_id": self.assignment_primary.pk,
                "operator_id": self.operator_conflict.pk,
                "force_override": "1",
            },
        )

        self.assertRedirects(response, url)

        self.assignment_primary.refresh_from_db()
        self.assertEqual(self.assignment_primary.operator, self.operator_conflict)
        self.assertEqual(self.assignment_primary.alert_level, AssignmentAlertLevel.CRITICAL)
        self.assertTrue(self.assignment_primary.is_overtime)
        self.assertEqual(self.assignment_primary.overtime_points, 1)
        self.assertFalse(self.assignment_primary.is_auto_assigned)

        messages = list(get_messages(response.wsgi_request))
        self.assertTrue(
            any("Asignación actualizada correctamente." in message.message for message in messages)
        )

    def test_create_assignment_accepts_manual_override_without_capability(self) -> None:
        url = reverse("calendario:calendar-detail", args=[self.calendar.pk])
        target_date = self.calendar.start_date + timedelta(days=1)

        response = self.client.post(
            url,
            data={
                "action": "create-assignment",
                "position_id": self.position_secondary.pk,
                "date": target_date.isoformat(),
                "operator_id": self.operator_manual.pk,
                "force_override": "1",
            },
        )

        self.assertRedirects(response, url)

        created_assignment = ShiftAssignment.objects.get(
            calendar=self.calendar,
            position=self.position_secondary,
            date=target_date,
        )

        self.assertEqual(created_assignment.operator, self.operator_manual)
        self.assertEqual(created_assignment.alert_level, AssignmentAlertLevel.CRITICAL)
        self.assertFalse(created_assignment.is_overtime)
        self.assertEqual(created_assignment.overtime_points, 0)
        self.assertFalse(created_assignment.is_auto_assigned)

        messages = list(get_messages(response.wsgi_request))
        self.assertTrue(
            any("Turno asignado manualmente." in message.message for message in messages)
        )


class CalendarDetailViewModifyCalendarTests(TestCase):
    def setUp(self) -> None:
        self.user = UserProfile.objects.create_user(
            cedula="3000",
            password="test",  # noqa: S106 - test credential
            nombres="Planificador",
            apellidos="Operaciones",
            telefono="3100000004",
        )
        self.client.force_login(self.user)

        self.farm = Farm.objects.create(name="Pinares")
        self.calendar = ShiftCalendar.objects.create(
            name="Semana aprobada",
            start_date=date(2025, 8, 4),
            end_date=date(2025, 8, 10),
            status=CalendarStatus.APPROVED,
            created_by=self.user,
            approved_by=self.user,
        )

    def test_mark_modified_changes_status(self) -> None:
        url = reverse("calendario:calendar-detail", args=[self.calendar.pk])
        response = self.client.post(
            url,
            data={
                "action": "mark-modified",
            },
        )

        self.assertRedirects(response, url)
        self.calendar.refresh_from_db()
        self.assertEqual(self.calendar.status, CalendarStatus.MODIFIED)

        messages = list(get_messages(response.wsgi_request))
        self.assertTrue(
            any("estado modificado" in message.message for message in messages)
        )

    def test_calendar_can_be_reapproved_and_modified_again(self) -> None:
        url = reverse("calendario:calendar-detail", args=[self.calendar.pk])

        # First modification
        self.client.post(url, data={"action": "mark-modified"})
        self.calendar.refresh_from_db()
        self.assertEqual(self.calendar.status, CalendarStatus.MODIFIED)

        # Re-approve
        response = self.client.post(url, data={"action": "approve"})
        self.assertRedirects(response, url)
        self.calendar.refresh_from_db()
        self.assertEqual(self.calendar.status, CalendarStatus.APPROVED)

        # Modify again
        response = self.client.post(url, data={"action": "mark-modified"})
        self.assertRedirects(response, url)
        self.calendar.refresh_from_db()
        self.assertEqual(self.calendar.status, CalendarStatus.MODIFIED)

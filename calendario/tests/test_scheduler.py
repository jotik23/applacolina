from __future__ import annotations

from datetime import date

from django.test import TestCase

from calendario.models import (
    AssignmentAlertLevel,
    CalendarStatus,
    ComplexityLevel,
    OverloadAllowance,
    PositionCategory,
    PositionDefinition,
    RestRule,
    ShiftCalendar,
    ShiftType,
)
from calendario.services import CalendarScheduler
from granjas.models import Farm
from users.models import Role, UserProfile


class CalendarSchedulerTests(TestCase):
    def setUp(self) -> None:
        self.farm = Farm.objects.create(name="Colina 1")

        self.role = Role.objects.create(name=Role.RoleName.GALPONERO)
        self.operator = UserProfile.objects.create_user(
            cedula="123",
            password="test",  # noqa: S106 - Test credential
            nombres="Alex",
            apellidos="Forero",
            telefono="123456",
        )
        self.operator.roles.add(self.role)

        self.position = PositionDefinition.objects.create(
            name="Galponero Día",
            code="G1-DIA",
            category=PositionCategory.GALPONERO_PRODUCCION_DIA,
            farm=self.farm,
            shift_type=ShiftType.DAY,
            complexity=ComplexityLevel.INTERMEDIATE,
            allow_lower_complexity=False,
            valid_from=date(2025, 1, 1),
        )

        self.calendar = ShiftCalendar.objects.create(
            name="Semana 1",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 3),
            status=CalendarStatus.DRAFT,
        )

        RestRule.objects.create(
            role=self.role,
            shift_type=ShiftType.DAY,
            min_rest_frequency=6,
            min_consecutive_days=5,
            max_consecutive_days=6,
            post_shift_rest_days=0,
            monthly_rest_days=5,
        )

        OverloadAllowance.objects.create(
            role=self.role,
            max_consecutive_extra_days=3,
        )

    def test_scheduler_assigns_operator_with_matching_capability(self) -> None:
        from calendario.models import OperatorCapability

        OperatorCapability.objects.create(
            operator=self.operator,
            category=PositionCategory.GALPONERO_PRODUCCION_DIA,
            min_complexity=ComplexityLevel.BASIC,
            max_complexity=ComplexityLevel.ADVANCED,
            effective_from=date(2024, 12, 1),
        )

        scheduler = CalendarScheduler(self.calendar)
        decisions = scheduler.generate(commit=True)

        self.calendar.refresh_from_db()
        assignments = list(self.calendar.assignments.all())

        self.assertEqual(len(assignments), 3)
        self.assertEqual(sum(1 for decision in decisions if decision.operator), 3)
        self.assertTrue(all(assignment.operator == self.operator for assignment in assignments))
        self.assertTrue(all(assignment.alert_level == AssignmentAlertLevel.NONE for assignment in assignments))

    def test_scheduler_marks_gap_when_no_capability(self) -> None:
        scheduler = CalendarScheduler(self.calendar)
        decisions = scheduler.generate(commit=False)

        self.assertEqual(len(decisions), 3)
        self.assertTrue(all(decision.operator is None for decision in decisions))
        self.assertTrue(all(decision.alert_level == AssignmentAlertLevel.CRITICAL for decision in decisions))

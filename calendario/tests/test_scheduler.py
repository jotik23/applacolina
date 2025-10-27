from __future__ import annotations

from datetime import date, timedelta

from django.test import TestCase

from calendario.models import (
    AssignmentAlertLevel,
    CalendarStatus,
    ComplexityLevel,
    OperatorRestPeriod,
    PositionCategory,
    PositionCategoryCode,
    PositionDefinition,
    RestPeriodSource,
    RestPeriodStatus,
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
                "rest_max_consecutive_days": 6,
                "rest_post_shift_days": 0,
                "rest_monthly_days": 5,
            },
        )

        self.category.extra_day_limit = 3
        self.category.overtime_points = 1
        self.category.overload_alert_level = AssignmentAlertLevel.WARN
        self.category.rest_min_frequency = 6
        self.category.rest_min_consecutive_days = 5
        self.category.rest_max_consecutive_days = 6
        self.category.rest_post_shift_days = 0
        self.category.rest_monthly_days = 5
        self.category.save()

        self.position = PositionDefinition.objects.create(
            name="Galponero Día",
            code="G1-DIA",
            category=self.category,
            farm=self.farm,
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

        self.operator.employment_start_date = date(2024, 12, 25)
        self.operator.save(update_fields=["employment_start_date"])


    def test_scheduler_assigns_operator_with_matching_capability(self) -> None:
        from calendario.models import OperatorCapability

        OperatorCapability.objects.create(
            operator=self.operator,
            category=self.category,
            skill_score=9,
        )

        scheduler = CalendarScheduler(self.calendar)
        decisions = scheduler.generate(commit=True)

        self.calendar.refresh_from_db()
        assignments = list(self.calendar.assignments.all())

        self.assertEqual(len(assignments), 3)
        self.assertEqual(sum(1 for decision in decisions if decision.operator), 3)
        self.assertTrue(all(assignment.operator == self.operator for assignment in assignments))
        self.assertTrue(all(assignment.alert_level == AssignmentAlertLevel.NONE for assignment in assignments))

    def test_scheduler_assigns_when_no_capabilities_exist(self) -> None:
        scheduler = CalendarScheduler(self.calendar)
        decisions = scheduler.generate(commit=True)

        assignments = list(self.calendar.assignments.all())

        self.assertEqual(len(decisions), 3)
        self.assertEqual(len(assignments), 3)
        self.assertTrue(all(decision.operator == self.operator for decision in decisions))
        self.assertTrue(all(decision.alert_level == AssignmentAlertLevel.NONE for decision in decisions))
        self.assertTrue(all(assignment.operator == self.operator for assignment in assignments))

    def test_scheduler_warns_when_skill_is_below_threshold_but_allowed(self) -> None:
        from calendario.models import OperatorCapability

        self.position.allow_lower_complexity = True
        self.position.save(update_fields=["allow_lower_complexity"])

        OperatorCapability.objects.create(
            operator=self.operator,
            category=self.category,
            skill_score=2,
        )

        scheduler = CalendarScheduler(self.calendar)
        decisions = scheduler.generate(commit=True)

        alerts = {decision.alert_level for decision in decisions}
        self.assertIn(AssignmentAlertLevel.WARN, alerts)
        self.assertNotIn(AssignmentAlertLevel.CRITICAL, alerts)

    def test_scheduler_skips_inactive_positions(self) -> None:
        self.position.is_active = False
        self.position.save(update_fields=["is_active"])

        scheduler = CalendarScheduler(self.calendar)
        decisions = scheduler.generate(commit=True)

        self.assertEqual(decisions, [])
        self.assertFalse(self.calendar.assignments.exists())

    def test_scheduler_respects_position_validity_range(self) -> None:
        self.position.valid_until = self.calendar.start_date + timedelta(days=1)
        self.position.save(update_fields=["valid_until"])

        scheduler = CalendarScheduler(self.calendar)
        decisions = scheduler.generate(commit=True)

        self.assertEqual(len(decisions), 2)
        assigned_dates = {assignment.date for assignment in self.calendar.assignments.all()}
        self.assertSetEqual(
            assigned_dates,
            {
                self.calendar.start_date,
                self.calendar.start_date + timedelta(days=1),
            },
        )

    def test_scheduler_respects_planned_rest_period(self) -> None:
        from calendario.models import OperatorCapability

        OperatorCapability.objects.create(
            operator=self.operator,
            category=self.category,
            skill_score=7,
        )

        OperatorRestPeriod.objects.create(
            operator=self.operator,
            start_date=self.calendar.start_date,
            end_date=self.calendar.start_date,
            status=RestPeriodStatus.APPROVED,
            source=RestPeriodSource.MANUAL,
        )

        scheduler = CalendarScheduler(self.calendar)
        decisions = scheduler.generate()

        self.assertIsNone(decisions[0].operator)

    def test_sync_rest_periods_creates_calendar_period(self) -> None:
        from calendario.models import OperatorCapability

        OperatorCapability.objects.create(
            operator=self.operator,
            category=self.category,
            skill_score=8,
        )

        self.position.valid_until = self.calendar.start_date + timedelta(days=1)
        self.position.save(update_fields=["valid_until"])

        scheduler = CalendarScheduler(self.calendar)
        scheduler.generate(commit=True)

        rest_periods = OperatorRestPeriod.objects.filter(
            calendar=self.calendar,
            source=RestPeriodSource.CALENDAR,
        )
        self.assertEqual(rest_periods.count(), 1)
        rest_period = rest_periods.first()
        self.assertIsNotNone(rest_period)
        assert rest_period is not None
        self.assertEqual(rest_period.start_date, self.calendar.start_date + timedelta(days=2))
        self.assertEqual(rest_period.end_date, self.calendar.start_date + timedelta(days=2))

from __future__ import annotations

from datetime import date

from django.test import TestCase

from calendario.models import (
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
from calendario.services import CalendarScheduler
from granjas.models import Farm
from users.models import UserProfile


class CalendarSchedulerTests(TestCase):
    def setUp(self) -> None:
        self.farm = Farm.objects.create(name="Colina Principal")
        self.category = PositionCategory.objects.create(
            code=PositionCategoryCode.GALPONERO_PRODUCCION_DIA,
            shift_type=ShiftType.DAY,
            rest_max_consecutive_days=8,
            rest_post_shift_days=0,
            rest_monthly_days=5,
        )
        self.position = PositionDefinition.objects.create(
            name="Galpón A",
            code="GPA-001",
            category=self.category,
            farm=self.farm,
            valid_from=date(2025, 10, 1),
        )

        self.primary_operator = UserProfile.objects.create_user(
            cedula="1001",
            password="pass",  # noqa: S106 - credencial de prueba
            nombres="Ana",
            apellidos="Ramírez",
            telefono="3000000000",
        )
        self.backup_operator = UserProfile.objects.create_user(
            cedula="1002",
            password="pass",  # noqa: S106 - credencial de prueba
            nombres="Bruno",
            apellidos="Salazar",
            telefono="3000000001",
        )
        self.primary_operator.suggested_positions.add(self.position)
        self.backup_operator.suggested_positions.add(self.position)

    def test_generate_prefers_last_assigned_operator(self) -> None:
        previous_calendar = ShiftCalendar.objects.create(
            name="Semana previa",
            start_date=date(2025, 10, 14),
            end_date=date(2025, 10, 16),
            status=CalendarStatus.APPROVED,
        )
        ShiftAssignment.objects.create(
            calendar=previous_calendar,
            position=self.position,
            date=date(2025, 10, 16),
            operator=self.primary_operator,
            alert_level=AssignmentAlertLevel.NONE,
            is_auto_assigned=True,
        )

        target_calendar = ShiftCalendar.objects.create(
            name="Semana objetivo",
            start_date=date(2025, 10, 21),
            end_date=date(2025, 10, 22),
            status=CalendarStatus.DRAFT,
        )

        scheduler = CalendarScheduler(target_calendar)
        decisions = scheduler.generate()

        assigned_ids = [decision.operator.id if decision.operator else None for decision in decisions]
        self.assertEqual(assigned_ids, [self.primary_operator.id, self.primary_operator.id])

    def test_generate_respects_manual_rest_period(self) -> None:
        OperatorRestPeriod.objects.create(
            operator=self.primary_operator,
            start_date=date(2025, 10, 21),
            end_date=date(2025, 10, 21),
            status=RestPeriodStatus.APPROVED,
            source=RestPeriodSource.MANUAL,
        )

        target_calendar = ShiftCalendar.objects.create(
            name="Semana con descanso manual",
            start_date=date(2025, 10, 21),
            end_date=date(2025, 10, 22),
            status=CalendarStatus.DRAFT,
        )

        scheduler = CalendarScheduler(target_calendar)
        decisions = scheduler.generate()

        self.assertEqual(len(decisions), 2)
        self.assertEqual(decisions[0].operator, self.backup_operator)
        self.assertEqual(decisions[1].operator, self.primary_operator)

    def test_commit_creates_rest_for_post_shift_rule(self) -> None:
        self.category.rest_post_shift_days = 1
        self.category.save(update_fields=["rest_post_shift_days"])

        self.primary_operator.suggested_positions.clear()
        self.backup_operator.suggested_positions.clear()

        solo_operator = UserProfile.objects.create_user(
            cedula="1003",
            password="pass",  # noqa: S106 - credencial de prueba
            nombres="Carla",
            apellidos="Torres",
            telefono="3000000002",
        )
        solo_operator.suggested_positions.add(self.position)

        target_calendar = ShiftCalendar.objects.create(
            name="Semana con posturno",
            start_date=date(2025, 10, 21),
            end_date=date(2025, 10, 22),
            status=CalendarStatus.DRAFT,
        )

        scheduler = CalendarScheduler(target_calendar)
        decisions = scheduler.generate(commit=True)

        self.assertEqual(len(decisions), 2)
        assignments = list(target_calendar.assignments.order_by("date"))
        self.assertEqual(len(assignments), 1)
        self.assertEqual(assignments[0].operator, solo_operator)

        self.assertEqual(decisions[0].operator, solo_operator)
        self.assertIsNone(decisions[1].operator)

        rest_periods = list(
            target_calendar.rest_periods.filter(source=RestPeriodSource.CALENDAR).order_by("start_date")
        )
        self.assertEqual(len(rest_periods), 1)
        self.assertEqual(rest_periods[0].start_date, date(2025, 10, 22))
        self.assertEqual(rest_periods[0].end_date, date(2025, 10, 22))

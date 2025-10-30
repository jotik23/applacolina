from __future__ import annotations

from datetime import date, timedelta
from unittest import mock

from django.test import TestCase

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
    AssignmentDecision,
)
from personal.services import CalendarScheduler
from production.models import Farm
from personal.models import UserProfile


class CalendarSchedulerTests(TestCase):
    def setUp(self) -> None:
        self.farm = Farm.objects.create(name="Colina Principal")
        self.category, created = PositionCategory.objects.get_or_create(
            code=PositionCategoryCode.GALPONERO_PRODUCCION_DIA,
            defaults={
                "shift_type": ShiftType.DAY,
                "rest_max_consecutive_days": 8,
                "rest_post_shift_days": 0,
                "rest_monthly_days": 5,
            },
        )
        if not created:
            update_fields: list[str] = []
            if self.category.shift_type != ShiftType.DAY:
                self.category.shift_type = ShiftType.DAY
                update_fields.append("shift_type")
            if self.category.rest_max_consecutive_days != 8:
                self.category.rest_max_consecutive_days = 8
                update_fields.append("rest_max_consecutive_days")
            if self.category.rest_post_shift_days != 0:
                self.category.rest_post_shift_days = 0
                update_fields.append("rest_post_shift_days")
            if self.category.rest_monthly_days != 5:
                self.category.rest_monthly_days = 5
                update_fields.append("rest_monthly_days")
            if update_fields:
                self.category.save(update_fields=update_fields)
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

    def test_generate_enforces_unique_operator_per_day(self) -> None:
        second_position = PositionDefinition.objects.create(
            name="Galpón B",
            code="GPB-002",
            category=self.category,
            farm=self.farm,
            valid_from=date(2025, 10, 1),
        )
        self.primary_operator.suggested_positions.add(second_position)

        target_calendar = ShiftCalendar.objects.create(
            name="Semana con duplicados",
            start_date=date(2025, 10, 21),
            end_date=date(2025, 10, 21),
            status=CalendarStatus.DRAFT,
        )

        duplicate_decisions = [
            AssignmentDecision(
                position=self.position,
                operator=self.primary_operator,
                date=target_calendar.start_date,
            ),
            AssignmentDecision(
                position=second_position,
                operator=self.primary_operator,
                date=target_calendar.start_date,
            ),
        ]

        scheduler = CalendarScheduler(target_calendar)
        with mock.patch.object(CalendarScheduler, "_plan_schedule", return_value=duplicate_decisions):
            decisions = scheduler.generate()

        self.assertEqual(decisions[0].operator, self.primary_operator)
        self.assertIsNone(decisions[1].operator)
        self.assertEqual(decisions[1].alert_level, AssignmentAlertLevel.CRITICAL)
        self.assertIn("ya tenía turno", decisions[1].notes)

    def test_respects_rest_max_consecutive_days(self) -> None:
        self.category.rest_max_consecutive_days = 2
        self.category.rest_monthly_days = 5
        self.category.save(update_fields=["rest_max_consecutive_days", "rest_monthly_days"])

        self.backup_operator.suggested_positions.clear()

        target_calendar = ShiftCalendar.objects.create(
            name="Semana con descanso obligatorio",
            start_date=date(2025, 10, 21),
            end_date=date(2025, 10, 23),
            status=CalendarStatus.DRAFT,
        )

        scheduler = CalendarScheduler(target_calendar)
        decisions = scheduler.generate(commit=True)

        assignments = list(target_calendar.assignments.order_by("date"))
        self.assertEqual(len(assignments), 2)
        self.assertEqual([assignment.operator for assignment in assignments], [self.primary_operator, self.primary_operator])

        self.assertEqual(len(decisions), 3)
        self.assertIsNone(decisions[-1].operator)
        self.assertEqual(decisions[-1].alert_level, AssignmentAlertLevel.CRITICAL)

        rest_periods = list(
            target_calendar.rest_periods.filter(source=RestPeriodSource.CALENDAR).order_by("start_date")
        )
        self.assertEqual(len(rest_periods), 1)
        self.assertEqual(rest_periods[0].start_date, date(2025, 10, 23))
        self.assertEqual(rest_periods[0].end_date, date(2025, 10, 23))

    def test_rest_skipped_when_monthly_quota_reached(self) -> None:
        self.category.rest_max_consecutive_days = 1
        self.category.rest_monthly_days = 1
        self.category.save(update_fields=["rest_max_consecutive_days", "rest_monthly_days"])

        OperatorRestPeriod.objects.create(
            operator=self.primary_operator,
            start_date=date(2025, 10, 20),
            end_date=date(2025, 10, 20),
            status=RestPeriodStatus.APPROVED,
            source=RestPeriodSource.MANUAL,
        )

        target_calendar = ShiftCalendar.objects.create(
            name="Semana sin descansos extra",
            start_date=date(2025, 10, 21),
            end_date=date(2025, 10, 22),
            status=CalendarStatus.DRAFT,
        )

        scheduler = CalendarScheduler(target_calendar)
        decisions = scheduler.generate(commit=True)

        assignments = list(target_calendar.assignments.order_by("date"))
        self.assertEqual(len(assignments), 2)
        self.assertTrue(all(assignment.operator == self.primary_operator for assignment in assignments))

        self.assertEqual(len(decisions), 2)
        self.assertTrue(all(decision.operator == self.primary_operator for decision in decisions))

        rest_periods = list(
            target_calendar.rest_periods.filter(source=RestPeriodSource.CALENDAR).order_by("start_date")
        )
        self.assertEqual(len(rest_periods), 0)

    def test_generate_flips_night_to_day_after_rest(self) -> None:
        night_category, created = PositionCategory.objects.get_or_create(
            code=PositionCategoryCode.GALPONERO_PRODUCCION_NOCHE,
            defaults={
                "shift_type": ShiftType.NIGHT,
                "rest_max_consecutive_days": 2,
                "rest_post_shift_days": 0,
                "rest_monthly_days": 5,
            },
        )
        if not created:
            update_fields: list[str] = []
            if night_category.shift_type != ShiftType.NIGHT:
                night_category.shift_type = ShiftType.NIGHT
                update_fields.append("shift_type")
            if night_category.rest_max_consecutive_days != 2:
                night_category.rest_max_consecutive_days = 2
                update_fields.append("rest_max_consecutive_days")
            if night_category.rest_post_shift_days != 0:
                night_category.rest_post_shift_days = 0
                update_fields.append("rest_post_shift_days")
            if night_category.rest_monthly_days != 5:
                night_category.rest_monthly_days = 5
                update_fields.append("rest_monthly_days")
            if update_fields:
                night_category.save(update_fields=update_fields)

        calendar_start = date(2025, 11, 1)
        night_position = PositionDefinition.objects.create(
            name="Vigilante Nocturno",
            code="VIG-NT-001",
            category=night_category,
            farm=self.farm,
            valid_from=calendar_start,
            display_order=1,
        )
        day_position = PositionDefinition.objects.create(
            name="Vigilante Diurno",
            code="VIG-DI-001",
            category=self.category,
            farm=self.farm,
            valid_from=calendar_start + timedelta(days=3),
            display_order=2,
        )

        hybrid_operator = UserProfile.objects.create_user(
            cedula="2001",
            password="pass",  # noqa: S106 - credencial de prueba
            nombres="Camila",
            apellidos="Aldana",
            telefono="3000001000",
        )
        hybrid_operator.suggested_positions.add(night_position, day_position)

        night_backup = UserProfile.objects.create_user(
            cedula="2002",
            password="pass",  # noqa: S106 - credencial de prueba
            nombres="Diego",
            apellidos="Zuluaga",
            telefono="3000001001",
        )
        night_backup.suggested_positions.add(night_position)

        target_calendar = ShiftCalendar.objects.create(
            name="Flip noche a día",
            start_date=calendar_start,
            end_date=calendar_start + timedelta(days=4),
            status=CalendarStatus.DRAFT,
        )

        scheduler = CalendarScheduler(target_calendar)
        decisions = scheduler.generate()

        assignments_by_date: dict[date, dict[str, Optional[UserProfile]]] = {}
        for decision in decisions:
            day_assignments = assignments_by_date.setdefault(decision.date, {})
            day_assignments[decision.position.code] = decision.operator

        day1 = calendar_start
        day2 = calendar_start + timedelta(days=1)
        day3 = calendar_start + timedelta(days=2)
        day4 = calendar_start + timedelta(days=3)
        day5 = calendar_start + timedelta(days=4)

        self.assertEqual(assignments_by_date[day1]["VIG-NT-001"], hybrid_operator)
        self.assertEqual(assignments_by_date[day2]["VIG-NT-001"], hybrid_operator)
        self.assertEqual(assignments_by_date[day3]["VIG-NT-001"], night_backup)
        self.assertEqual(assignments_by_date[day4]["VIG-NT-001"], night_backup)
        self.assertEqual(assignments_by_date[day4]["VIG-DI-001"], hybrid_operator)
        self.assertEqual(assignments_by_date[day5]["VIG-DI-001"], hybrid_operator)

    def test_generate_continues_same_shift_without_opposite_option(self) -> None:
        night_category, created = PositionCategory.objects.get_or_create(
            code=PositionCategoryCode.GALPONERO_LEVANTE_NOCHE,
            defaults={
                "shift_type": ShiftType.NIGHT,
                "rest_max_consecutive_days": 2,
                "rest_post_shift_days": 0,
                "rest_monthly_days": 5,
            },
        )
        if not created:
            update_fields: list[str] = []
            if night_category.shift_type != ShiftType.NIGHT:
                night_category.shift_type = ShiftType.NIGHT
                update_fields.append("shift_type")
            if night_category.rest_max_consecutive_days != 2:
                night_category.rest_max_consecutive_days = 2
                update_fields.append("rest_max_consecutive_days")
            if night_category.rest_post_shift_days != 0:
                night_category.rest_post_shift_days = 0
                update_fields.append("rest_post_shift_days")
            if night_category.rest_monthly_days != 5:
                night_category.rest_monthly_days = 5
                update_fields.append("rest_monthly_days")
            if update_fields:
                night_category.save(update_fields=update_fields)

        calendar_start = date(2025, 11, 8)
        night_position = PositionDefinition.objects.create(
            name="Supervisor Noche",
            code="SUP-NT-001",
            category=night_category,
            farm=self.farm,
            valid_from=calendar_start,
            display_order=1,
        )

        night_only = UserProfile.objects.create_user(
            cedula="2010",
            password="pass",  # noqa: S106 - credencial de prueba
            nombres="Elena",
            apellidos="Arango",
            telefono="3000001010",
        )
        night_only.suggested_positions.add(night_position)

        night_backup = UserProfile.objects.create_user(
            cedula="2011",
            password="pass",  # noqa: S106 - credencial de prueba
            nombres="Fabio",
            apellidos="Zamora",
            telefono="3000001011",
        )
        night_backup.suggested_positions.add(night_position)

        target_calendar = ShiftCalendar.objects.create(
            name="Continuidad nocturna",
            start_date=calendar_start,
            end_date=calendar_start + timedelta(days=4),
            status=CalendarStatus.DRAFT,
        )

        scheduler = CalendarScheduler(target_calendar)
        decisions = scheduler.generate()

        assignments_by_date: dict[date, dict[str, Optional[UserProfile]]] = {}
        for decision in decisions:
            day_assignments = assignments_by_date.setdefault(decision.date, {})
            day_assignments[decision.position.code] = decision.operator

        day1 = calendar_start
        day2 = calendar_start + timedelta(days=1)
        day3 = calendar_start + timedelta(days=2)
        day4 = calendar_start + timedelta(days=3)

        self.assertEqual(assignments_by_date[day1]["SUP-NT-001"], night_only)
        self.assertEqual(assignments_by_date[day2]["SUP-NT-001"], night_only)
        self.assertEqual(assignments_by_date[day3]["SUP-NT-001"], night_backup)
        self.assertEqual(assignments_by_date[day4]["SUP-NT-001"], night_only)

    def test_generate_flips_day_to_night_after_rest(self) -> None:
        day_category, created = PositionCategory.objects.get_or_create(
            code=PositionCategoryCode.CLASIFICADOR_DIA,
            defaults={
                "shift_type": ShiftType.DAY,
                "rest_max_consecutive_days": 2,
                "rest_post_shift_days": 0,
                "rest_monthly_days": 5,
            },
        )
        if not created:
            update_fields: list[str] = []
            if day_category.shift_type != ShiftType.DAY:
                day_category.shift_type = ShiftType.DAY
                update_fields.append("shift_type")
            if day_category.rest_max_consecutive_days != 2:
                day_category.rest_max_consecutive_days = 2
                update_fields.append("rest_max_consecutive_days")
            if day_category.rest_post_shift_days != 0:
                day_category.rest_post_shift_days = 0
                update_fields.append("rest_post_shift_days")
            if day_category.rest_monthly_days != 5:
                day_category.rest_monthly_days = 5
                update_fields.append("rest_monthly_days")
            if update_fields:
                day_category.save(update_fields=update_fields)

        night_category, created = PositionCategory.objects.get_or_create(
            code=PositionCategoryCode.CLASIFICADOR_NOCHE,
            defaults={
                "shift_type": ShiftType.NIGHT,
                "rest_max_consecutive_days": 2,
                "rest_post_shift_days": 0,
                "rest_monthly_days": 5,
            },
        )
        if not created:
            update_fields: list[str] = []
            if night_category.shift_type != ShiftType.NIGHT:
                night_category.shift_type = ShiftType.NIGHT
                update_fields.append("shift_type")
            if night_category.rest_max_consecutive_days != 2:
                night_category.rest_max_consecutive_days = 2
                update_fields.append("rest_max_consecutive_days")
            if night_category.rest_post_shift_days != 0:
                night_category.rest_post_shift_days = 0
                update_fields.append("rest_post_shift_days")
            if night_category.rest_monthly_days != 5:
                night_category.rest_monthly_days = 5
                update_fields.append("rest_monthly_days")
            if update_fields:
                night_category.save(update_fields=update_fields)

        calendar_start = date(2025, 11, 15)
        day_position = PositionDefinition.objects.create(
            name="Operario Día",
            code="OP-DI-001",
            category=day_category,
            farm=self.farm,
            valid_from=calendar_start,
            display_order=1,
        )
        night_position = PositionDefinition.objects.create(
            name="Operario Noche",
            code="OP-NT-001",
            category=night_category,
            farm=self.farm,
            valid_from=calendar_start + timedelta(days=3),
            display_order=2,
        )

        hybrid_operator = UserProfile.objects.create_user(
            cedula="2020",
            password="pass",  # noqa: S106 - credencial de prueba
            nombres="Gina",
            apellidos="Almonacid",
            telefono="3000001020",
        )
        hybrid_operator.suggested_positions.add(day_position, night_position)

        day_backup = UserProfile.objects.create_user(
            cedula="2021",
            password="pass",  # noqa: S106 - credencial de prueba
            nombres="Héctor",
            apellidos="Barrera",
            telefono="3000001021",
        )
        day_backup.suggested_positions.add(day_position)

        target_calendar = ShiftCalendar.objects.create(
            name="Flip día a noche",
            start_date=calendar_start,
            end_date=calendar_start + timedelta(days=4),
            status=CalendarStatus.DRAFT,
        )

        scheduler = CalendarScheduler(target_calendar)
        decisions = scheduler.generate()

        assignments_by_date: dict[date, dict[str, Optional[UserProfile]]] = {}
        for decision in decisions:
            day_assignments = assignments_by_date.setdefault(decision.date, {})
            day_assignments[decision.position.code] = decision.operator

        day1 = calendar_start
        day2 = calendar_start + timedelta(days=1)
        day3 = calendar_start + timedelta(days=2)
        day4 = calendar_start + timedelta(days=3)

        self.assertEqual(assignments_by_date[day1]["OP-DI-001"], hybrid_operator)
        self.assertEqual(assignments_by_date[day2]["OP-DI-001"], hybrid_operator)
        self.assertEqual(assignments_by_date[day3]["OP-DI-001"], day_backup)
        self.assertEqual(assignments_by_date[day4]["OP-DI-001"], day_backup)
        self.assertEqual(assignments_by_date[day4]["OP-NT-001"], hybrid_operator)

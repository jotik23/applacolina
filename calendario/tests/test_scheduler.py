from __future__ import annotations

from datetime import date, timedelta

from django.test import TestCase

from calendario.models import (
    AssignmentAlertLevel,
    CalendarStatus,
    ComplexityLevel,
    DayOfWeek,
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

    def test_scheduler_skips_category_automatic_rest_days(self) -> None:
        from calendario.models import OperatorCapability

        OperatorCapability.objects.create(
            operator=self.operator,
            category=self.category,
            skill_score=8,
        )

        self.category.automatic_rest_days = [DayOfWeek.SATURDAY]
        self.category.save(update_fields=["automatic_rest_days"])

        weekend_calendar = ShiftCalendar.objects.create(
            name="Fin de semana",
            start_date=date(2025, 1, 3),
            end_date=date(2025, 1, 5),
            status=CalendarStatus.DRAFT,
        )

        scheduler = CalendarScheduler(weekend_calendar)
        decisions = scheduler.generate(commit=True)

        assigned_dates = {assignment.date for assignment in weekend_calendar.assignments.all()}
        self.assertEqual(len(decisions), 2)
        self.assertNotIn(date(2025, 1, 4), assigned_dates)
        self.assertTrue(all(decision.date != date(2025, 1, 4) for decision in decisions))

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

    def test_scheduler_prioritizes_more_critical_positions(self) -> None:
        from calendario.models import OperatorCapability

        OperatorCapability.objects.create(
            operator=self.operator,
            category=self.category,
            skill_score=10,
        )

        self.position.complexity = ComplexityLevel.CRITICAL
        self.position.display_order = 5
        self.position.save(update_fields=["complexity", "display_order"])

        support_position = PositionDefinition.objects.create(
            name="Apoyo",
            code="SUP-1",
            category=self.category,
            farm=self.farm,
            complexity=ComplexityLevel.SUPPORT,
            allow_lower_complexity=True,
            valid_from=self.calendar.start_date,
        )
        support_position.display_order = 1
        support_position.save(update_fields=["display_order"])

        scheduler = CalendarScheduler(self.calendar)
        decisions = scheduler.generate()

        critical_assignments = [
            decision for decision in decisions if decision.position.id == self.position.id
        ]
        support_assignments = [
            decision for decision in decisions if decision.position.id == support_position.id
        ]

        self.assertTrue(critical_assignments)
        self.assertTrue(support_assignments)
        self.assertTrue(all(decision.operator == self.operator for decision in critical_assignments))
        self.assertTrue(all(decision.operator is None for decision in support_assignments))

    def test_scheduler_prioritizes_continuous_assignments_when_possible(self) -> None:
        from calendario.models import OperatorCapability

        second_operator = UserProfile.objects.create_user(
            cedula="124",
            password="test",  # noqa: S106 - Test credential
            nombres="Jamie",
            apellidos="Lopez",
            telefono="987654",
        )
        second_operator.roles.add(self.role)

        OperatorCapability.objects.create(
            operator=self.operator,
            category=self.category,
            skill_score=8,
        )
        OperatorCapability.objects.create(
            operator=second_operator,
            category=self.category,
            skill_score=8,
        )

        scheduler = CalendarScheduler(self.calendar)
        scheduler.generate(commit=True)

        assignments = list(
            self.calendar.assignments.filter(position=self.position).order_by("date")
        )
        self.assertEqual(len(assignments), 3)
        self.assertEqual(assignments[0].operator_id, assignments[1].operator_id)

    def test_scheduler_populates_weekend_assignments(self) -> None:
        from calendario.models import OperatorCapability

        second_operator = UserProfile.objects.create_user(
            cedula="125",
            password="test",  # noqa: S106 - Test credential
            nombres="Taylor",
            apellidos="Rios",
            telefono="555123",
        )
        second_operator.roles.add(self.role)

        OperatorCapability.objects.create(
            operator=self.operator,
            category=self.category,
            skill_score=8,
        )
        OperatorCapability.objects.create(
            operator=second_operator,
            category=self.category,
            skill_score=8,
        )

        week_calendar = ShiftCalendar.objects.create(
            name="Semana completa",
            start_date=date(2025, 1, 6),
            end_date=date(2025, 1, 12),
            status=CalendarStatus.DRAFT,
        )

        scheduler = CalendarScheduler(week_calendar)
        scheduler.generate(commit=True)

        assignments = list(
            week_calendar.assignments.filter(position=self.position).order_by("date")
        )

        weekend_assignments = [
            assignment
            for assignment in assignments
            if assignment.date.weekday() in {DayOfWeek.SATURDAY, DayOfWeek.SUNDAY}
        ]
        self.assertTrue(weekend_assignments)


    def test_scheduler_respects_employment_end_date(self) -> None:
        from calendario.models import OperatorCapability

        employment_end = self.calendar.start_date + timedelta(days=1)
        self.operator.employment_end_date = employment_end
        self.operator.save(update_fields=["employment_end_date"])

        extended_calendar = ShiftCalendar.objects.create(
            name="Semana extendida",
            start_date=self.calendar.start_date,
            end_date=self.calendar.start_date + timedelta(days=4),
            status=CalendarStatus.DRAFT,
        )

        OperatorCapability.objects.create(
            operator=self.operator,
            category=self.category,
            skill_score=8,
        )

        scheduler = CalendarScheduler(extended_calendar)
        scheduler.generate(commit=True)

        assignment_dates = list(
            extended_calendar.assignments.filter(operator=self.operator).values_list("date", flat=True)
        )

        self.assertTrue(assignment_dates)
        self.assertTrue(all(date_value <= employment_end for date_value in assignment_dates))
        self.assertFalse(
            extended_calendar.assignments.filter(date__gt=employment_end).exists()
        )

        rest_periods = OperatorRestPeriod.objects.filter(
            operator=self.operator,
            source=RestPeriodSource.CALENDAR,
            calendar=extended_calendar,
        )
        self.assertTrue(
            all(period.end_date <= employment_end for period in rest_periods),
            "Rest periods should not extend beyond employment end date.",
        )

    def test_consecutive_night_assignments_followed_by_rest_block(self) -> None:
        from calendario.models import OperatorCapability

        # Create an additional collaborator for coverage after the rest block.
        backup_operator = UserProfile.objects.create_user(
            cedula="126",
            password="test",  # noqa: S106 - Test credential
            nombres="Beatriz",
            apellidos="Nocturna",
            telefono="321654",
        )
        backup_operator.roles.add(self.role)

        night_category, _created = PositionCategory.objects.get_or_create(
            code=PositionCategoryCode.GALPONERO_PRODUCCION_NOCHE,
            defaults={
                "name": "Galponero producción noche",
                "shift_type": ShiftType.NIGHT,
                "extra_day_limit": 1,
                "overtime_points": 1,
                "overload_alert_level": AssignmentAlertLevel.WARN,
                "rest_min_frequency": 7,
                "rest_min_consecutive_days": 1,
                "rest_max_consecutive_days": 7,
                "rest_post_shift_days": 1,
                "rest_monthly_days": 5,
            },
        )
        night_category.extra_day_limit = 1
        night_category.rest_min_consecutive_days = 1
        night_category.rest_max_consecutive_days = 7
        night_category.rest_post_shift_days = 1
        night_category.save()

        night_position = PositionDefinition.objects.create(
            name="Turno noche",
            code="NOC-1",
            category=night_category,
            farm=self.farm,
            complexity=ComplexityLevel.INTERMEDIATE,
            allow_lower_complexity=False,
            valid_from=date(2025, 1, 1),
        )

        night_calendar = ShiftCalendar.objects.create(
            name="Noche extendida",
            start_date=date(2025, 2, 1),
            end_date=date(2025, 2, 10),
            status=CalendarStatus.DRAFT,
        )

        OperatorCapability.objects.create(
            operator=self.operator,
            category=night_category,
            skill_score=8,
        )
        OperatorCapability.objects.create(
            operator=backup_operator,
            category=night_category,
            skill_score=8,
        )

        scheduler = CalendarScheduler(night_calendar)
        scheduler.generate(commit=True)

        assignments = list(
            night_calendar.assignments.filter(position=night_position).order_by("date")
        )
        self.assertEqual(len(assignments), 10)

        primary_operator = assignments[0].operator
        first_run_dates = [assignment.date for assignment in assignments if assignment.operator == primary_operator]

        expected_first_run = [
            night_calendar.start_date + timedelta(days=offset) for offset in range(7)
        ]
        self.assertGreaterEqual(
            len(first_run_dates),
            7,
            "Expected the primary operator to cover the first seven nights consecutively.",
        )
        self.assertEqual(first_run_dates[:7], expected_first_run)

        rest_window = {
            night_calendar.start_date + timedelta(days=7),
            night_calendar.start_date + timedelta(days=8),
        }
        self.assertTrue(rest_window.isdisjoint(first_run_dates))

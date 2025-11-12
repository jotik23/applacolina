from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.test import TestCase

from administration.services.payroll import build_payroll_summary, resolve_payroll_period, PayrollComputationError
from personal.models import (
    CalendarStatus,
    OperatorRestPeriod,
    OperatorSalary,
    PositionCategory,
    PositionCategoryCode,
    PositionDefinition,
    Role,
    ShiftAssignment,
    ShiftCalendar,
    ShiftType,
    UserProfile,
)
from production.models import Farm


class PayrollServiceTests(TestCase):
    def setUp(self):
        self.farm = Farm.objects.create(name="Colina")
        self.category, _ = PositionCategory.objects.get_or_create(
            code=PositionCategoryCode.GALPONERO_PRODUCCION_DIA,
            defaults={"shift_type": ShiftType.DAY},
        )
        self.position = PositionDefinition.objects.create(
            name="Turno Día",
            code="POS-1",
            category=self.category,
            farm=self.farm,
            valid_from=date(2024, 1, 1),
        )
        self.calendar = ShiftCalendar.objects.create(
            name="Abril",
            start_date=date(2025, 4, 1),
            end_date=date(2025, 4, 30),
            status=CalendarStatus.APPROVED,
        )
        self.role, _ = Role.objects.get_or_create(name=Role.RoleName.GALPONERO)

    def test_monthly_payroll_applies_extra_rest_deduction(self):
        operator = self._create_operator(
            cedula="100",
            payment_type=OperatorSalary.PaymentType.MONTHLY,
            amount=Decimal("1200000"),
        )
        work_days = [
            date(2025, 4, 16),
            date(2025, 4, 17),
            date(2025, 4, 18),
            date(2025, 4, 19),
            date(2025, 4, 24),
            date(2025, 4, 25),
            date(2025, 4, 26),
            date(2025, 4, 27),
        ]
        self._assign_days(operator, work_days)
        OperatorRestPeriod.objects.create(
            operator=operator,
            start_date=date(2025, 4, 20),
            end_date=date(2025, 4, 23),
        )
        period = resolve_payroll_period(date(2025, 4, 16), date(2025, 4, 30))

        summary = build_payroll_summary(period=period)
        entry = summary.entries[0]

        self.assertEqual(entry.rest_days, 4)
        self.assertEqual(entry.extra_rest_count, 2)
        self.assertEqual(entry.discounted_extra_count, 2)
        self.assertEqual(entry.suggested_amount, Decimal("520000.00"))

        extra_tokens = {detail.token for detail in entry.rest_details if detail.is_extra}
        bonus_token = next(iter(extra_tokens))
        summary_with_bonus = build_payroll_summary(period=period, bonified_rest_tokens={bonus_token})
        entry_bonus = summary_with_bonus.entries[0]
        self.assertEqual(entry_bonus.discounted_extra_count, 1)
        self.assertEqual(entry_bonus.suggested_amount, Decimal("560000.00"))

    def test_daily_payment_counts_worked_days(self):
        operator = self._create_operator(
            cedula="200",
            payment_type=OperatorSalary.PaymentType.DAILY,
            amount=Decimal("50000"),
        )
        work_days = [date(2025, 4, day) for day in (1, 2, 3, 4, 5)]
        self._assign_days(operator, work_days)
        period = resolve_payroll_period(date(2025, 4, 1), date(2025, 4, 15))

        summary = build_payroll_summary(period=period)
        entry = summary.entries[0]
        self.assertEqual(entry.worked_days, 5)
        self.assertEqual(entry.suggested_amount, Decimal("250000.00"))
        self.assertEqual(entry.deduction_amount, Decimal("0.00"))

    def test_raises_error_when_operator_has_no_salary(self):
        operator = UserProfile.objects.create(
            cedula="300",
            nombres="Sin",
            apellidos="Salario",
            telefono="3000000",
            employment_start_date=date(2024, 1, 1),
        )
        operator.roles.add(self.role)
        self._assign_days(operator, [date(2025, 4, 1)])
        period = resolve_payroll_period(date(2025, 4, 1), date(2025, 4, 15))

        with self.assertRaises(PayrollComputationError):
            build_payroll_summary(period=period)

    def _create_operator(self, *, cedula: str, payment_type: str, amount: Decimal) -> UserProfile:
        operator = UserProfile.objects.create(
            cedula=cedula,
            nombres=f"Operario {cedula}",
            apellidos="Test",
            telefono=f"{cedula}000",
            employment_start_date=date(2024, 1, 1),
        )
        operator.roles.add(self.role)
        OperatorSalary.objects.create(
            operator=operator,
            amount=amount,
            payment_type=payment_type,
            effective_from=date(2024, 1, 1),
        )
        return operator

    def _assign_days(self, operator: UserProfile, days: list[date]) -> None:
        for target_date in days:
            ShiftAssignment.objects.create(
                calendar=self.calendar,
                position=self.position,
                date=target_date,
                operator=operator,
            )

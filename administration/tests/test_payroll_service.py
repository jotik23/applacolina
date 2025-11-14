from __future__ import annotations

from datetime import date, timedelta
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
    PositionJobType,
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
            job_type=PositionJobType.PRODUCTION,
        )
        self.calendar = ShiftCalendar.objects.create(
            name="Abril",
            start_date=date(2025, 4, 1),
            end_date=date(2025, 4, 30),
            status=CalendarStatus.APPROVED,
        )
    def test_monthly_payroll_applies_extra_rest_deduction(self):
        operator = self._create_operator(
            cedula="100",
            payment_type=OperatorSalary.PaymentType.MONTHLY,
            amount=Decimal("1200000"),
        )
        work_days = [
            date(2025, 4, 16) + timedelta(days=offset)
            for offset in range(15)
            if date(2025, 4, 16) + timedelta(days=offset) not in {
                date(2025, 4, 20),
                date(2025, 4, 21),
                date(2025, 4, 22),
                date(2025, 4, 23),
            }
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
        self.assertEqual(entry.job_type, PositionJobType.PRODUCTION)
        self.assertEqual(entry.farm_label, self.farm.name)
        self.assertEqual(summary.totals_by_job_type[0].job_type_label, "Producción")
        self.assertEqual(summary.totals_by_farm[0].farm_label, self.farm.name)

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
        self._assign_days(operator, [date(2025, 4, 1)])
        period = resolve_payroll_period(date(2025, 4, 1), date(2025, 4, 15))

        with self.assertRaises(PayrollComputationError):
            build_payroll_summary(period=period)

    def test_rest_only_operator_grouped_under_otros(self):
        operator = self._create_operator(
            cedula="400",
            payment_type=OperatorSalary.PaymentType.MONTHLY,
            amount=Decimal("1000000"),
        )
        OperatorRestPeriod.objects.create(
            operator=operator,
            start_date=date(2025, 4, 1),
            end_date=date(2025, 4, 2),
        )
        period = resolve_payroll_period(date(2025, 4, 1), date(2025, 4, 15))

        summary = build_payroll_summary(period=period)
        self.assertEqual(len(summary.entries), 1)
        entry = summary.entries[0]
        self.assertIsNone(entry.job_type)
        self.assertEqual(entry.job_type_label, "Sin tipo")
        self.assertEqual(entry.farm_label, "Otros")
        self.assertEqual(summary.totals_by_farm[0].farm_label, "Otros")

    def test_rest_allowance_respects_salary_configuration(self):
        operator = self._create_operator(
            cedula="500",
            payment_type=OperatorSalary.PaymentType.MONTHLY,
            amount=Decimal("1200000"),
            rest_days_per_week=2,
        )
        # 4 descansos, con 2 por semana y 15 días => 4 descansos permitidos.
        OperatorRestPeriod.objects.create(
            operator=operator,
            start_date=date(2025, 4, 1),
            end_date=date(2025, 4, 4),
        )
        period = resolve_payroll_period(date(2025, 4, 1), date(2025, 4, 15))

        summary = build_payroll_summary(period=period)
        entry = summary.entries[0]
        self.assertEqual(entry.extra_rest_count, 0)
        self.assertEqual(entry.discounted_extra_count, 0)

    def test_non_worked_days_trigger_deduction_and_support_bonification(self):
        operator = self._create_operator(
            cedula="600",
            payment_type=OperatorSalary.PaymentType.MONTHLY,
            amount=Decimal("900000"),
        )
        work_days = [date(2025, 4, day) for day in (1, 2, 3, 4, 5)]
        self._assign_days(operator, work_days)
        period = resolve_payroll_period(date(2025, 4, 1), date(2025, 4, 15))

        summary = build_payroll_summary(period=period)
        entry = summary.entries[0]
        self.assertEqual(entry.non_worked_count, 10)
        per_day_value = (operator.salary_records.first().amount / Decimal("2") / Decimal("15")).quantize(Decimal("0.01"))
        expected_deduction = (per_day_value * Decimal(entry.non_worked_count)).quantize(Decimal("0.01"))
        self.assertEqual(entry.deduction_amount, expected_deduction)

        bonus_token = entry.non_worked_details[0].token
        summary_bonus = build_payroll_summary(
            period=period,
            bonified_idle_tokens={bonus_token},
        )
        entry_bonus = summary_bonus.entries[0]
        self.assertEqual(entry_bonus.discounted_non_worked_count, entry.non_worked_count - 1)

    def test_operator_without_assignments_uses_suggested_positions_for_job_type(self):
        operator = self._create_operator(
            cedula="700",
            payment_type=OperatorSalary.PaymentType.MONTHLY,
            amount=Decimal("1000000"),
        )
        classification_category, _ = PositionCategory.objects.get_or_create(
            code=PositionCategoryCode.CLASIFICADOR_DIA,
            defaults={"shift_type": ShiftType.DAY},
        )
        classification_position = PositionDefinition.objects.create(
            name="Clasificación día",
            code="POS-CLAS",
            category=classification_category,
            farm=self.farm,
            valid_from=date(2024, 1, 1),
            job_type=PositionJobType.CLASSIFICATION,
        )
        operator.suggested_positions.add(classification_position)

        period = resolve_payroll_period(date(2025, 4, 1), date(2025, 4, 15))
        summary = build_payroll_summary(period=period)

        entry = summary.entries[0]
        self.assertEqual(entry.job_type, PositionJobType.CLASSIFICATION)
        self.assertEqual(entry.job_type_label, "Clasificación")
        self.assertIn("Clasificación", [total.job_type_label for total in summary.totals_by_job_type])

    def test_operator_without_assignments_uses_roles_for_job_type(self):
        operator = self._create_operator(
            cedula="800",
            payment_type=OperatorSalary.PaymentType.MONTHLY,
            amount=Decimal("1100000"),
        )
        admin_role = Role.objects.create(name=Role.RoleName.ADMINISTRADOR)
        operator.roles.add(admin_role)

        period = resolve_payroll_period(date(2025, 4, 1), date(2025, 4, 15))
        summary = build_payroll_summary(period=period)

        entry = summary.entries[0]
        self.assertEqual(entry.job_type, PositionJobType.ADMINISTRATIVE)
        self.assertEqual(entry.job_type_label, "Administración")

    def test_job_type_inferred_from_position_category(self):
        operator = self._create_operator(
            cedula="900",
            payment_type=OperatorSalary.PaymentType.MONTHLY,
            amount=Decimal("1300000"),
        )
        administrative_category, _ = PositionCategory.objects.get_or_create(
            code=PositionCategoryCode.ADMINISTRADOR,
            defaults={"shift_type": ShiftType.DAY},
        )
        administrative_position = PositionDefinition.objects.create(
            name="Administrador general",
            code="POS-ADMIN",
            category=administrative_category,
            farm=self.farm,
            valid_from=date(2024, 1, 1),
            job_type=PositionJobType.PRODUCTION,
        )
        ShiftAssignment.objects.create(
            calendar=self.calendar,
            position=administrative_position,
            date=date(2025, 4, 2),
            operator=operator,
        )

        period = resolve_payroll_period(date(2025, 4, 1), date(2025, 4, 15))
        summary = build_payroll_summary(period=period)

        entry = summary.entries[0]
        self.assertEqual(entry.job_type, PositionJobType.ADMINISTRATIVE)
        self.assertEqual(entry.job_type_label, "Administración")

    def _create_operator(
        self,
        *,
        cedula: str,
        payment_type: str,
        amount: Decimal,
        rest_days_per_week: int = 1,
    ) -> UserProfile:
        operator = UserProfile.objects.create(
            cedula=cedula,
            nombres=f"Operario {cedula}",
            apellidos="Test",
            telefono=f"{cedula}000",
            employment_start_date=date(2024, 1, 1),
        )
        OperatorSalary.objects.create(
            operator=operator,
            amount=amount,
            payment_type=payment_type,
            effective_from=date(2024, 1, 1),
            rest_days_per_week=rest_days_per_week,
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

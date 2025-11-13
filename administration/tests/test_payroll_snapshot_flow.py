from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from administration.models import PayrollSnapshot
from administration.services.payroll import build_payroll_summary, resolve_payroll_period
from administration.services.payroll_snapshot import deserialize_payroll_summary, serialize_payroll_summary
from personal.models import (
    CalendarStatus,
    OperatorSalary,
    PositionCategory,
    PositionCategoryCode,
    PositionDefinition,
    PositionJobType,
    ShiftAssignment,
    ShiftCalendar,
    ShiftType,
    UserProfile,
)
from production.models import Farm


class PayrollSnapshotTestDataMixin:
    def setUp(self):
        super().setUp()
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
        self.period = resolve_payroll_period(date(2025, 4, 1), date(2025, 4, 15))

    def _create_operator(self, cedula: str, amount: Decimal) -> UserProfile:
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
            payment_type=OperatorSalary.PaymentType.MONTHLY,
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


class PayrollSnapshotSerializationTests(PayrollSnapshotTestDataMixin, TestCase):
    def test_roundtrip_preserves_summary(self):
        operator = self._create_operator("800", Decimal("1200000"))
        self._assign_days(operator, [date(2025, 4, day) for day in range(1, 6)])

        summary = build_payroll_summary(period=self.period)
        payload = serialize_payroll_summary(summary)
        restored = deserialize_payroll_summary(payload)

        self.assertEqual(restored.overall_total, summary.overall_total)
        self.assertEqual(len(restored.entries), 1)
        self.assertEqual(restored.entries[0].operator.pk, operator.pk)
        self.assertEqual(restored.entries[0].worked_days, 5)


class PayrollManagementViewTests(PayrollSnapshotTestDataMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.staff_user = UserProfile.objects.create_superuser(
            cedula="999",
            password="testpass",
            nombres="Admin",
            apellidos="User",
            telefono="9990000",
        )
        self.operator = self._create_operator("801", Decimal("1000000"))
        self._assign_days(self.operator, [date(2025, 4, day) for day in range(1, 6)])

    def test_generate_action_creates_snapshot_and_renders_summary(self):
        self.client.force_login(self.staff_user)
        url = reverse("administration:purchases_payroll")
        response = self.client.post(
            url,
            {
                "start_date": self.period.start_date.isoformat(),
                "end_date": self.period.end_date.isoformat(),
                "form_action": "generate",
            },
        )
        self.assertEqual(response.status_code, 200)

        snapshot = PayrollSnapshot.objects.get(
            start_date=self.period.start_date,
            end_date=self.period.end_date,
        )
        self.assertTrue(snapshot.payload)
        self.assertIsNotNone(snapshot.last_computed_at)

        response = self.client.get(
            url,
            {
                "start_date": self.period.start_date.isoformat(),
                "end_date": self.period.end_date.isoformat(),
            },
        )
        self.assertContains(response, "Regenerar nómina")
        self.assertContains(response, self.operator.get_full_name())

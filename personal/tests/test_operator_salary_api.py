from __future__ import annotations

import json
from datetime import date

from django.test import TestCase
from django.urls import reverse

from personal.models import OperatorSalary, UserProfile


class OperatorSalaryApiTests(TestCase):
    def setUp(self) -> None:
        self.admin_user = UserProfile.objects.create_user(
            cedula="9090",
            password="test",  # noqa: S106 - test credential
            nombres="Coordinador",
            apellidos="Principal",
            telefono="3150000000",
            is_staff=True,
        )
        self.client.force_login(self.admin_user)
        self.operators_url = reverse("personal-api:calendar-operators")

    @staticmethod
    def _base_payload() -> dict[str, str]:
        return {
            "cedula": "1234567890",
            "telefono": "3001234567",
            "nombres": "Nuevo",
            "apellidos": "Colaborador",
            "employment_start_date": "2025-01-01",
            "employment_end_date": "",
            "access_key": "changeme",  # noqa: S106 - test credential
            "automatic_rest_days": [],
            "roles": [],
            "suggested_positions": [],
        }

    @staticmethod
    def _salary_payload() -> list[dict[str, str]]:
        return [
            {
                "amount": "150000",
                "payment_type": OperatorSalary.PaymentType.DAILY,
                "effective_from": "2025-01-01",
                "effective_until": "2025-02-28",
            },
            {
                "amount": "180000",
                "payment_type": OperatorSalary.PaymentType.MONTHLY,
                "effective_from": "2025-03-01",
                "effective_until": "",
            },
        ]

    def test_create_operator_requires_salary_payload(self) -> None:
        payload = self._base_payload()
        response = self.client.post(
            self.operators_url,
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertIn("salaries", body.get("errors", {}))

    def test_create_operator_persists_salary_history(self) -> None:
        payload = self._base_payload()
        payload["salaries"] = self._salary_payload()

        response = self.client.post(
            self.operators_url,
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()
        salaries_payload = body["operator"]["salaries"]
        self.assertEqual(len(salaries_payload), 2)
        self.assertEqual(salaries_payload[0]["effective_from"], "2025-03-01")
        self.assertEqual(salaries_payload[1]["effective_from"], "2025-01-01")

        operator = UserProfile.objects.get(cedula=payload["cedula"])
        salaries = OperatorSalary.objects.filter(operator=operator).order_by("effective_from")
        self.assertEqual(salaries.count(), 2)
        self.assertEqual(salaries.first().payment_type, OperatorSalary.PaymentType.DAILY)
        self.assertEqual(salaries.last().payment_type, OperatorSalary.PaymentType.MONTHLY)

    def test_update_operator_replaces_salary_history(self) -> None:
        operator = UserProfile.objects.create_user(
            cedula="445566",
            password="test",  # noqa: S106 - test credential
            nombres="Histórico",
            apellidos="Salarios",
            telefono="3000000100",
        )
        existing = OperatorSalary.objects.create(
            operator=operator,
            amount="120000",
            payment_type=OperatorSalary.PaymentType.DAILY,
            effective_from=date(2024, 12, 1),
        )
        detail_url = reverse("personal-api:calendar-operator-detail", args=[operator.pk])

        payload = {
            "nombres": "Actualizado",
            "salaries": [
                {
                    "id": existing.id,
                    "amount": "125000",
                    "payment_type": OperatorSalary.PaymentType.DAILY,
                    "effective_from": "2024-12-01",
                    "effective_until": "2024-12-31",
                },
                {
                    "amount": "140000",
                    "payment_type": OperatorSalary.PaymentType.MONTHLY,
                    "effective_from": "2025-01-01",
                    "effective_until": "",
                },
            ],
        }

        response = self.client.patch(
            detail_url,
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        salaries_payload = body["operator"]["salaries"]
        self.assertEqual([item["effective_from"] for item in salaries_payload], ["2025-01-01", "2024-12-01"])

        salaries = list(
            OperatorSalary.objects.filter(operator=operator).order_by("effective_from", "id")
        )
        self.assertEqual(len(salaries), 2)
        self.assertEqual(float(salaries[0].amount), 125000.0)
        self.assertEqual(float(salaries[1].amount), 140000.0)
        self.assertEqual(salaries[1].payment_type, OperatorSalary.PaymentType.MONTHLY)

    def test_update_without_salary_payload_keeps_existing_records(self) -> None:
        operator = UserProfile.objects.create_user(
            cedula="778899",
            password="test",  # noqa: S106 - test credential
            nombres="SinCambios",
            apellidos="Salario",
            telefono="3000000200",
        )
        OperatorSalary.objects.create(
            operator=operator,
            amount="110000",
            payment_type=OperatorSalary.PaymentType.DAILY,
            effective_from=date(2025, 1, 1),
        )
        detail_url = reverse("personal-api:calendar-operator-detail", args=[operator.pk])

        response = self.client.patch(
            detail_url,
            data=json.dumps({"nombres": "Nuevo Nombre"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(OperatorSalary.objects.filter(operator=operator).count(), 1)

    def test_update_without_salaries_and_without_existing_records_fails(self) -> None:
        operator = UserProfile.objects.create_user(
            cedula="990011",
            password="test",  # noqa: S106 - test credential
            nombres="SinSalario",
            apellidos="Pendiente",
            telefono="3000000300",
        )
        detail_url = reverse("personal-api:calendar-operator-detail", args=[operator.pk])

        response = self.client.patch(
            detail_url,
            data=json.dumps({"nombres": "Intento"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertIn("salaries", body.get("errors", {}))

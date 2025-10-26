from __future__ import annotations

import json
from datetime import date

from django.test import TestCase
from django.urls import reverse

from calendario.models import (
    ComplexityLevel,
    PositionCategory,
    PositionCategoryCode,
    PositionDefinition,
    ShiftType,
)
from granjas.models import Farm
from users.models import UserProfile


class PositionOrderingApiTests(TestCase):
    def setUp(self) -> None:
        self.user = UserProfile.objects.create_user(
            cedula="8001",
            password="test",  # noqa: S106 - test credential
            nombres="Laura",
            apellidos="García",
            telefono="3000000002",
        )
        self.client.force_login(self.user)
        self.farm = Farm.objects.create(name="Colina Test")

        self.category, _created = PositionCategory.objects.get_or_create(
            code=PositionCategoryCode.GALPONERO_PRODUCCION_DIA,
            defaults={
                "name": "Galponero producción día",
                "shift_type": ShiftType.DAY,
                "extra_day_limit": 3,
                "overtime_points": 1,
                "overload_alert_level": "warn",
                "rest_min_frequency": 6,
                "rest_min_consecutive_days": 5,
                "rest_max_consecutive_days": 8,
                "rest_post_shift_days": 0,
                "rest_monthly_days": 5,
            },
        )
        self.category.extra_day_limit = 3
        self.category.overtime_points = 1
        self.category.overload_alert_level = "warn"
        self.category.rest_min_frequency = 6
        self.category.rest_min_consecutive_days = 5
        self.category.rest_max_consecutive_days = 8
        self.category.rest_post_shift_days = 0
        self.category.rest_monthly_days = 5
        self.category.save()

        self.position_a = PositionDefinition.objects.create(
            name="Posición A",
            code="POS-A",
            category=self.category,
            farm=self.farm,
            complexity=ComplexityLevel.BASIC,
            valid_from=date(2025, 1, 1),
        )
        self.position_b = PositionDefinition.objects.create(
            name="Posición B",
            code="POS-B",
            category=self.category,
            farm=self.farm,
            complexity=ComplexityLevel.BASIC,
            valid_from=date(2025, 1, 1),
        )
        self.position_c = PositionDefinition.objects.create(
            name="Posición C",
            code="POS-C",
            category=self.category,
            farm=self.farm,
            complexity=ComplexityLevel.BASIC,
            valid_from=date(2025, 1, 1),
        )
        self.reorder_url = reverse("calendario-api:calendar-position-reorder")

    def test_positions_receive_sequential_order_on_creation(self) -> None:
        self.assertEqual(
            [
                self.position_a.display_order,
                self.position_b.display_order,
                self.position_c.display_order,
            ],
            [1, 2, 3],
        )

    def test_reorder_updates_display_order_and_returns_sorted_payload(self) -> None:
        payload = {
            "order": [self.position_c.id, self.position_a.id, self.position_b.id],
        }
        response = self.client.post(
            self.reorder_url,
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)

        body = response.json()
        self.assertIn("positions", body)
        returned_ids = [item["id"] for item in body["positions"][:3]]
        self.assertEqual(returned_ids, payload["order"])

        self.position_a.refresh_from_db()
        self.position_b.refresh_from_db()
        self.position_c.refresh_from_db()

        self.assertEqual(self.position_c.display_order, 1)
        self.assertEqual(self.position_a.display_order, 2)
        self.assertEqual(self.position_b.display_order, 3)

        metadata_url = reverse("calendario-api:calendar-metadata")
        metadata_response = self.client.get(metadata_url)
        self.assertEqual(metadata_response.status_code, 200)
        metadata_positions = metadata_response.json().get("positions", [])
        ordered_ids = [item["id"] for item in metadata_positions[:3]]
        self.assertEqual(ordered_ids, payload["order"])

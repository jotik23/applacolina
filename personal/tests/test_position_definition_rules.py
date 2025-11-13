from __future__ import annotations

from django.test import TestCase

from personal.forms import PositionDefinitionForm
from personal.models import (
    PositionCategory,
    PositionCategoryCode,
    PositionJobType,
    ShiftType,
)
from production.models import Farm


class PositionDefinitionFormTests(TestCase):
    def setUp(self) -> None:
        self.farm = Farm.objects.create(name="Colina 1")
        self.production_category = self._ensure_category(PositionCategoryCode.GALPONERO_PRODUCCION_DIA, ShiftType.DAY)
        self.classification_category = self._ensure_category(PositionCategoryCode.CLASIFICADOR_DIA, ShiftType.DAY)
        self.admin_category = self._ensure_category(PositionCategoryCode.ADMINISTRADOR, ShiftType.DAY)

    @staticmethod
    def _ensure_category(code: str, shift_type: str) -> PositionCategory:
        category, _ = PositionCategory.objects.get_or_create(
            code=code,
            defaults={
                "shift_type": shift_type,
                "rest_max_consecutive_days": 8,
                "rest_post_shift_days": 0,
                "rest_monthly_days": 5,
                "is_active": True,
            },
        )
        return category

    def _build_payload(self, **overrides: str) -> dict[str, str]:
        payload = {
            "name": "Test position",
            "job_type": PositionJobType.PRODUCTION,
            "category": str(self.production_category.id),
            "farm": str(self.farm.id),
            "chicken_house": "",
            "rooms": [],
            "valid_from": "2025-01-01",
            "valid_until": "",
            "handoff_position": "",
        }
        payload.update(overrides)
        return payload

    def test_requires_matching_category_for_job_type(self) -> None:
        payload = self._build_payload(
            job_type=PositionJobType.ADMINISTRATIVE,
            category=str(self.production_category.id),
            farm="",
        )
        form = PositionDefinitionForm(payload)
        self.assertFalse(form.is_valid())
        self.assertIn("category", form.errors)

    def test_requires_farm_for_categories_that_need_location(self) -> None:
        payload = self._build_payload(
            job_type=PositionJobType.PRODUCTION,
            category=str(self.production_category.id),
            farm="",
        )
        form = PositionDefinitionForm(payload)
        self.assertFalse(form.is_valid())
        self.assertIn("farm", form.errors)

    def test_allows_missing_farm_for_administrative_roles(self) -> None:
        payload = self._build_payload(
            job_type=PositionJobType.ADMINISTRATIVE,
            category=str(self.admin_category.id),
            farm="",
        )
        form = PositionDefinitionForm(payload)
        self.assertTrue(form.is_valid(), form.errors)

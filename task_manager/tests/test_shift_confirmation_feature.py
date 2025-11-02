from __future__ import annotations

from datetime import date, timedelta

from django.test import TestCase
from django.utils.formats import date_format

from personal.models import (
    CalendarStatus,
    PositionCategory,
    PositionCategoryCode,
    PositionDefinition,
    ShiftAssignment,
    ShiftCalendar,
    ShiftType,
    UserProfile,
)
from production.models import ChickenHouse, Farm, Room
from task_manager.mini_app.features import build_shift_confirmation_card, build_shift_confirmation_empty_card
from task_manager.services import suppress_task_assignment_sync


class ShiftConfirmationFeatureTests(TestCase):
    def setUp(self):
        self.farm = Farm.objects.create(name="Granja Experimental")
        self.chicken_house = ChickenHouse.objects.create(
            farm=self.farm,
            name="Galpón Principal",
            area_m2=320.0,
        )
        self.room_a = Room.objects.create(
            chicken_house=self.chicken_house,
            name="Sala Alfa",
            area_m2=120.0,
        )
        self.room_b = Room.objects.create(
            chicken_house=self.chicken_house,
            name="Sala Beta",
            area_m2=120.0,
        )
        self.category = PositionCategory.objects.create(
            code=PositionCategoryCode.SUPERVISOR,
            shift_type=ShiftType.DAY,
        )
        self.position = PositionDefinition.objects.create(
            name="Supervisor de Bioseguridad",
            code="SUP-BIO-001",
            category=self.category,
            farm=self.farm,
            chicken_house=self.chicken_house,
            valid_from=date(2024, 10, 1),
            valid_until=date(2024, 12, 31),
        )
        self.position.rooms.set([self.room_a, self.room_b])

        self.operator = UserProfile.objects.create_user(
            "100100100",
            password=None,
            nombres="Ana María",
            apellidos="Ramírez",
            telefono="300000001",
        )
        self.prev_operator = UserProfile.objects.create_user(
            "100100101",
            password=None,
            nombres="Carlos",
            apellidos="Soto",
            telefono="300000002",
        )
        self.next_operator = UserProfile.objects.create_user(
            "100100102",
            password=None,
            nombres="Lucía",
            apellidos="Nieto",
            telefono="300000003",
        )

    def test_build_shift_confirmation_card_with_assignments(self):
        reference_date = date(2024, 11, 2)
        calendar = ShiftCalendar.objects.create(
            name="Q4 2024",
            start_date=reference_date - timedelta(days=1),
            end_date=reference_date + timedelta(days=1),
            status=CalendarStatus.APPROVED,
        )

        next_position = PositionDefinition.objects.create(
            name="Supervisor entrante",
            code="SUP-BIO-002",
            category=self.category,
            farm=self.farm,
            chicken_house=self.chicken_house,
            valid_from=date(2024, 10, 1),
            valid_until=date(2024, 12, 31),
        )
        previous_position = PositionDefinition.objects.create(
            name="Supervisor saliente",
            code="SUP-BIO-003",
            category=self.category,
            farm=self.farm,
            chicken_house=self.chicken_house,
            valid_from=date(2024, 10, 1),
            valid_until=date(2024, 12, 31),
            handoff_position=self.position,
        )
        self.position.handoff_position = next_position
        self.position.save(update_fields=["handoff_position"])

        with suppress_task_assignment_sync():
            ShiftAssignment.objects.create(
                calendar=calendar,
                position=previous_position,
                date=reference_date - timedelta(days=1),
                operator=self.prev_operator,
            )
            assignment = ShiftAssignment.objects.create(
                calendar=calendar,
                position=self.position,
                date=reference_date,
                operator=self.operator,
            )
            ShiftAssignment.objects.create(
                calendar=calendar,
                position=next_position,
                date=reference_date,
                operator=self.next_operator,
            )

        card = build_shift_confirmation_card(user=self.operator, reference_date=reference_date)
        self.assertIsNotNone(card)
        assert card  # For type checkers

        self.assertEqual(card.assignment_id, assignment.pk)
        self.assertEqual(card.calendar_id, calendar.pk)
        self.assertEqual(card.date, reference_date)
        self.assertIn("Hola Ana María", card.greeting_label)
        self.assertIn(reference_date.strftime("%d"), card.greeting_label)
        self.assertIn("nov", card.greeting_label.lower())
        self.assertEqual(card.date_label, date_format(reference_date, "DATE_FORMAT"))
        expected_summary = f"{self.category.display_name} · {self.position.name}"
        self.assertEqual(card.summary_label, expected_summary)
        self.assertEqual(card.category_label, self.category.display_name)
        self.assertEqual(card.position_label, self.position.name)
        self.assertEqual(card.farm, self.farm.name)
        self.assertEqual(card.barn, self.chicken_house.name)
        self.assertEqual(card.rooms, sorted([self.room_a.name, self.room_b.name]))
        self.assertEqual(card.handoff_to, self.next_operator.get_full_name())
        self.assertTrue(card.requires_confirmation)
        self.assertFalse(card.confirmed)
        self.assertIn(str(assignment.pk), card.storage_key)
        self.assertIn(reference_date.isoformat(), card.storage_key)

    def test_returns_none_when_no_assignment_exists(self):
        reference_date = date(2024, 11, 2)
        result = build_shift_confirmation_card(user=self.operator, reference_date=reference_date)
        self.assertIsNone(result)

        empty_card = build_shift_confirmation_empty_card(user=self.operator, reference_date=reference_date)
        self.assertIsNotNone(empty_card)
        assert empty_card  # type checkers
        self.assertIn("no encontramos", empty_card.body_lines[0].lower())

    def test_adjacent_assignments_prioritizes_modified_but_allows_draft(self):
        reference_date = date(2024, 11, 2)
        base_calendar = ShiftCalendar.objects.create(
            name="Q4 2024",
            start_date=reference_date - timedelta(days=1),
            end_date=reference_date + timedelta(days=1),
            status=CalendarStatus.APPROVED,
        )
        draft_calendar = ShiftCalendar.objects.create(
            name="Q4 2024 borrador",
            start_date=reference_date - timedelta(days=1),
            end_date=reference_date + timedelta(days=1),
            status=CalendarStatus.DRAFT,
        )
        modified_calendar = ShiftCalendar.objects.create(
            name="Q4 2024 ajustes",
            start_date=reference_date - timedelta(days=1),
            end_date=reference_date + timedelta(days=1),
            status=CalendarStatus.MODIFIED,
            base_calendar=base_calendar,
        )

        with suppress_task_assignment_sync():
            ShiftAssignment.objects.create(
                calendar=draft_calendar,
                position=self.position,
                date=reference_date - timedelta(days=1),
                operator=self.prev_operator,
            )
            ShiftAssignment.objects.create(
                calendar=base_calendar,
                position=self.position,
                date=reference_date,
                operator=self.operator,
            )
            ShiftAssignment.objects.create(
                calendar=modified_calendar,
                position=self.position,
                date=reference_date + timedelta(days=1),
                operator=self.next_operator,
            )

        card = build_shift_confirmation_card(user=self.operator, reference_date=reference_date)
        self.assertIsNotNone(card)
        assert card

        self.assertEqual(card.handoff_to, self.next_operator.get_full_name())

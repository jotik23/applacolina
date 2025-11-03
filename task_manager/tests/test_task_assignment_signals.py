from __future__ import annotations

from datetime import date, timedelta
from unittest import mock

from django.test import TestCase

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
from task_manager.models import TaskCategory, TaskDefinition, TaskStatus


class TaskAssignmentSignalTests(TestCase):
    def setUp(self):
        self.status = TaskStatus.objects.create(name="Activa", is_active=True)
        self.category = TaskCategory.objects.create(name="General", is_active=True)
        self.farm = Farm.objects.create(name="El Vergel")
        self.chicken_house = ChickenHouse.objects.create(
            farm=self.farm,
            name="Galpón 1",
        )
        self.room = Room.objects.create(
            chicken_house=self.chicken_house,
            name="Sala 1",
            area_m2=50,
        )
        self.position_category = PositionCategory.objects.create(
            code=PositionCategoryCode.SUPERVISOR,
            shift_type=ShiftType.DAY,
        )
        self.position = PositionDefinition.objects.create(
            name="Supervisor general",
            code="SUP-GEN-001",
            category=self.position_category,
            farm=self.farm,
            chicken_house=self.chicken_house,
            valid_from=date(2024, 1, 1),
        )
        self.position.rooms.add(self.room)
        self.operator = UserProfile.objects.create_user(
            "123456789",
            password=None,
            nombres="Luis",
            apellidos="Pérez",
            telefono="3001112233",
        )
        self.calendar = ShiftCalendar.objects.create(
            name="Calendario Enero",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            status=CalendarStatus.APPROVED,
        )

    @mock.patch("task_manager.signals._schedule_range_sync")
    def test_task_definition_post_save_triggers_sync(self, schedule_mock: mock.Mock) -> None:
        scheduled_date = date(2024, 1, 5)
        TaskDefinition.objects.create(
            name="Visita de inspección",
            status=self.status,
            category=self.category,
            task_type=TaskDefinition.TaskType.ONE_TIME,
            scheduled_for=scheduled_date,
        )
        schedule_mock.assert_called_with(scheduled_date, scheduled_date)

    @mock.patch("task_manager.signals._schedule_range_sync")
    def test_task_definition_scope_change_triggers_sync(self, schedule_mock: mock.Mock) -> None:
        task = TaskDefinition.objects.create(
            name="Recorrido semanal",
            status=self.status,
            category=self.category,
            task_type=TaskDefinition.TaskType.ONE_TIME,
            scheduled_for=date(2024, 1, 7),
        )
        schedule_mock.reset_mock()
        task.rooms.add(self.room)
        schedule_mock.assert_called()

    @mock.patch("task_manager.signals._schedule_range_sync")
    def test_shift_assignment_save_triggers_sync_for_current_and_previous_date(
        self, schedule_mock: mock.Mock
    ) -> None:
        assignment = ShiftAssignment.objects.create(
            calendar=self.calendar,
            position=self.position,
            date=date(2024, 1, 10),
            operator=self.operator,
        )
        schedule_mock.assert_called_with(assignment.date, assignment.date)

        schedule_mock.reset_mock()
        old_date = assignment.date
        new_date = old_date + timedelta(days=1)
        assignment.date = new_date
        assignment.save(update_fields=["date"])

        scheduled_ranges = {tuple(call.args) for call in schedule_mock.call_args_list}
        self.assertEqual(
            scheduled_ranges,
            {
                (old_date, old_date),
                (new_date, new_date),
            },
        )

    @mock.patch("task_manager.signals._schedule_range_sync")
    def test_shift_assignment_delete_triggers_sync(self, schedule_mock: mock.Mock) -> None:
        assignment = ShiftAssignment.objects.create(
            calendar=self.calendar,
            position=self.position,
            date=date(2024, 1, 12),
            operator=self.operator,
        )
        schedule_mock.reset_mock()
        assignment.delete()
        schedule_mock.assert_called_with(date(2024, 1, 12), date(2024, 1, 12))

from __future__ import annotations

from datetime import date

from django.test import TestCase

from personal.models import (
    CalendarStatus,
    DayOfWeek,
    PositionCategory,
    PositionCategoryCode,
    PositionDefinition,
    ShiftAssignment,
    ShiftCalendar,
    ShiftType,
    UserProfile,
)
from production.models import ChickenHouse, Farm, Room
from task_manager.models import TaskAssignment, TaskCategory, TaskDefinition, TaskStatus
from task_manager.services import suppress_task_assignment_sync, sync_task_assignments


class TaskAssignmentSynchronizationTests(TestCase):
    def setUp(self):
        self.farm = Farm.objects.create(name="Granja Los Naranjos")
        self.chicken_house = ChickenHouse.objects.create(
            farm=self.farm,
            name="Galpón 1",
            area_m2=280.0,
        )
        self.room = Room.objects.create(
            chicken_house=self.chicken_house,
            name="Sala A",
            area_m2=120,
        )
        self.category = PositionCategory.objects.create(
            code=PositionCategoryCode.SUPERVISOR,
            shift_type=ShiftType.DAY,
        )
        self.position = PositionDefinition.objects.create(
            name="Supervisor turno día",
            code="SUP-DAY-001",
            category=self.category,
            farm=self.farm,
            chicken_house=self.chicken_house,
            valid_from=date(2024, 1, 1),
        )
        self.position.rooms.set([self.room])

        self.second_position = PositionDefinition.objects.create(
            name="Supervisor suplente",
            code="SUP-DAY-ALT",
            category=self.category,
            farm=self.farm,
            chicken_house=self.chicken_house,
            valid_from=date(2024, 1, 1),
        )
        self.second_position.rooms.set([self.room])

        self.calendar = ShiftCalendar.objects.create(
            name="Enero 2024",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            status=CalendarStatus.APPROVED,
        )

        self.operator = UserProfile.objects.create_user(
            "1020304050",
            password=None,
            nombres="Laura",
            apellidos="García",
            telefono="3110000000",
        )

        self.backup_operator = UserProfile.objects.create_user(
            "3020103040",
            password=None,
            nombres="Carlos",
            apellidos="Ramírez",
            telefono="3125550000",
        )

        self.status = TaskStatus.objects.create(name="Activa", is_active=True)
        self.category_task = TaskCategory.objects.create(name="Bioseguridad", is_active=True)

    def _create_task_definition(
        self,
        *,
        name: str = "Limpieza matutina",
        task_type: str = TaskDefinition.TaskType.RECURRING,
        weekly_days: list[int] | None = None,
        scheduled_for: date | None = None,
        **extra_fields,
    ) -> TaskDefinition:
        with suppress_task_assignment_sync():
            task = TaskDefinition.objects.create(
                name=name,
                status=self.status,
                category=self.category_task,
                task_type=task_type,
                scheduled_for=scheduled_for,
                **extra_fields,
            )
        if weekly_days is not None:
            with suppress_task_assignment_sync():
                task.weekly_days = [int(value) for value in weekly_days]
                task.save(update_fields=["weekly_days"])
        return task

    def test_sync_creates_assignments_for_matching_shifts(self):
        due_date = date(2024, 1, 8)  # Monday
        with suppress_task_assignment_sync():
            ShiftAssignment.objects.create(
                calendar=self.calendar,
                position=self.position,
                date=due_date,
                operator=self.operator,
            )

        task = self._create_task_definition(
            weekly_days=[DayOfWeek.MONDAY],
            position=self.position,
        )

        sync_task_assignments(start_date=due_date, end_date=due_date)

        assignment = TaskAssignment.objects.get(task_definition=task, due_date=due_date)
        self.assertEqual(assignment.collaborator, self.operator)

    def test_sync_marks_assignments_as_orphan_when_no_match_exists(self):
        due_date = date(2024, 1, 15)  # Monday
        with suppress_task_assignment_sync():
            shift_assignment = ShiftAssignment.objects.create(
                calendar=self.calendar,
                position=self.position,
                date=due_date,
                operator=self.operator,
            )

        task = self._create_task_definition(
            name="Desinfección semanal",
            weekly_days=[DayOfWeek.MONDAY],
            position=self.position,
        )

        sync_task_assignments(start_date=due_date, end_date=due_date)
        assignment = TaskAssignment.objects.get(task_definition=task, due_date=due_date)
        self.assertEqual(assignment.collaborator, self.operator)

        with suppress_task_assignment_sync():
            shift_assignment.delete()
        sync_task_assignments(start_date=due_date, end_date=due_date)

        assignment.refresh_from_db()
        self.assertIsNone(assignment.collaborator)

    def test_sync_replicates_tasks_for_each_matching_scope(self):
        due_date = date(2024, 1, 10)  # Wednesday

        with suppress_task_assignment_sync():
            ShiftAssignment.objects.create(
                calendar=self.calendar,
                position=self.position,
                date=due_date,
                operator=self.operator,
            )
            ShiftAssignment.objects.create(
                calendar=self.calendar,
                position=self.second_position,
                date=due_date,
                operator=self.backup_operator,
            )

        task = self._create_task_definition(
            name="Chequeo sanitario granja",
            weekly_days=[DayOfWeek.WEDNESDAY],
        )
        with suppress_task_assignment_sync():
            second_room = Room.objects.create(
                chicken_house=self.chicken_house,
                name="Sala B",
                area_m2=110,
            )
            self.second_position.rooms.set([second_room])
            task.rooms.set([self.room, second_room])

        sync_task_assignments(start_date=due_date, end_date=due_date)

        assignments = TaskAssignment.objects.filter(task_definition=task, due_date=due_date).order_by("collaborator_id")
        self.assertEqual(assignments.count(), 2)
        collaborator_ids = list(assignments.values_list("collaborator_id", flat=True))
        self.assertEqual(collaborator_ids, sorted([self.operator.pk, self.backup_operator.pk]))

    def test_sync_creates_orphan_for_one_time_task_without_shift(self):
        due_date = date(2024, 1, 5)
        task = self._create_task_definition(
            name="Inventario extraordinario",
            task_type=TaskDefinition.TaskType.ONE_TIME,
            scheduled_for=due_date,
            description="Revisión completa del inventario de insumos.",
        )

        sync_task_assignments(start_date=due_date, end_date=due_date)

        assignment = TaskAssignment.objects.get(task_definition=task, due_date=due_date)
        self.assertIsNone(assignment.collaborator)
        self.assertIsNone(assignment.previous_collaborator)

    def test_sync_uses_collaborator_fallback_when_scope_allows(self):
        due_date = date(2024, 1, 20)

        collaborator = UserProfile.objects.create_user(
            "1055512345",
            password=None,
            nombres="Elena",
            apellidos="Martínez",
            telefono="3130000000",
            employment_start_date=date(2023, 12, 1),
        )

        task = self._create_task_definition(
            name="Revisión de indicadores",
            task_type=TaskDefinition.TaskType.ONE_TIME,
            scheduled_for=due_date,
            collaborator=collaborator,
        )

        sync_task_assignments(start_date=due_date, end_date=due_date)

        assignment = TaskAssignment.objects.get(task_definition=task, due_date=due_date)
        self.assertEqual(assignment.collaborator, collaborator)

    def test_sync_reassigns_to_new_operator_with_overlapping_rooms(self):
        due_date = date(2024, 1, 8)

        with suppress_task_assignment_sync():
            shift = ShiftAssignment.objects.create(
                calendar=self.calendar,
                position=self.position,
                date=due_date,
                operator=self.operator,
            )

        task = self._create_task_definition(
            name="Revisión de puertas",
            weekly_days=[DayOfWeek.MONDAY],
        )
        with suppress_task_assignment_sync():
            task.rooms.set([self.room])

        sync_task_assignments(start_date=due_date, end_date=due_date)

        assignment = TaskAssignment.objects.get(task_definition=task, due_date=due_date)
        self.assertEqual(assignment.collaborator, self.operator)
        self.assertIsNone(assignment.previous_collaborator)

        with suppress_task_assignment_sync():
            shift.delete()
            ShiftAssignment.objects.create(
                calendar=self.calendar,
                position=self.second_position,
                date=due_date,
                operator=self.backup_operator,
            )

        sync_task_assignments(start_date=due_date, end_date=due_date)

        assignment.refresh_from_db()
        self.assertEqual(assignment.collaborator, self.backup_operator)
        self.assertEqual(assignment.previous_collaborator, self.operator)

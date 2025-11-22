from __future__ import annotations

from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from personal.models import (
    DayOfWeek,
    PositionCategory,
    PositionCategoryCode,
    PositionDefinition,
    ShiftType,
)
from production.models import ChickenHouse, Farm, Room
from task_manager.models import TaskAssignment, TaskCategory, TaskDefinition, TaskStatus
from task_manager.services import suppress_task_assignment_sync


class TaskDefinitionDuplicateViewTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.staff_user = user_model.objects.create_user(
            "900100",
            password="secret-pass",
            nombres="Admin",
            apellidos="Usuario",
            telefono="3110000000",
            is_staff=True,
        )
        self.client.force_login(self.staff_user)

        self.status = TaskStatus.objects.create(name="Activa")
        self.category = TaskCategory.objects.create(name="Sanidad")

        self.farm = Farm.objects.create(name="Granja Norte")
        self.chicken_house = ChickenHouse.objects.create(farm=self.farm, name="Galpón A")
        self.room_a = Room.objects.create(
            chicken_house=self.chicken_house,
            name="Sala 1",
            area_m2=120,
        )
        self.room_b = Room.objects.create(
            chicken_house=self.chicken_house,
            name="Sala 2",
            area_m2=140,
        )
        self.position_category, _ = PositionCategory.objects.get_or_create(
            code=PositionCategoryCode.SUPERVISOR,
            defaults={"shift_type": ShiftType.DAY},
        )
        self.position = PositionDefinition.objects.create(
            name="Supervisor general",
            code="SUP-GEN-001",
            category=self.position_category,
            farm=self.farm,
            chicken_house=self.chicken_house,
            valid_from=date(2024, 1, 1),
        )
        self.position.rooms.set([self.room_a])

        self.collaborator = user_model.objects.create_user(
            "900200",
            password=None,
            nombres="Laura",
            apellidos="García",
            telefono="3110000001",
        )

        self.known_task_ids = set(TaskDefinition.objects.values_list("pk", flat=True))

        with suppress_task_assignment_sync():
            self.task = TaskDefinition.objects.create(
                name="Inspección sanitaria",
                description="Revisión completa de bioseguridad.",
                status=self.status,
                category=self.category,
                is_mandatory=True,
                is_accumulative=True,
                criticality_level=TaskDefinition.CriticalityLevel.HIGH,
                task_type=TaskDefinition.TaskType.RECURRING,
                weekly_days=[DayOfWeek.MONDAY, DayOfWeek.FRIDAY],
                fortnight_days=[1, 10],
                month_days=[5, 15],
                monthly_week_days=[1, 3],
                position=self.position,
                collaborator=self.collaborator,
                evidence_requirement=TaskDefinition.EvidenceRequirement.PHOTO_OR_VIDEO,
                record_format=TaskDefinition.RecordFormat.PRODUCTION_RECORD,
            )
            self.task.rooms.set([self.room_a, self.room_b])
        self.known_task_ids.add(self.task.pk)

    def test_duplicate_task_definition_copies_all_fields(self):
        with suppress_task_assignment_sync():
            response = self.client.post(
                reverse("task_manager:definition-duplicate", args=[self.task.pk])
            )

        self.assertEqual(response.status_code, 302)
        duplicates = TaskDefinition.objects.exclude(pk__in=self.known_task_ids)
        self.assertEqual(duplicates.count(), 1)
        duplicated_task = duplicates.first()
        assert duplicated_task is not None
        self.known_task_ids.add(duplicated_task.pk)

        redirect_url = f"{reverse('task_manager:index')}?tm_task={duplicated_task.pk}#tm-tareas"
        self.assertEqual(response["Location"], redirect_url)

        self.assertNotEqual(duplicated_task.name, self.task.name)
        self.assertEqual(duplicated_task.description, self.task.description)
        self.assertEqual(duplicated_task.status, self.task.status)
        self.assertEqual(duplicated_task.category, self.task.category)
        self.assertEqual(duplicated_task.is_mandatory, self.task.is_mandatory)
        self.assertEqual(duplicated_task.is_accumulative, self.task.is_accumulative)
        self.assertEqual(
            duplicated_task.criticality_level,
            self.task.criticality_level,
        )
        self.assertEqual(duplicated_task.task_type, self.task.task_type)
        self.assertEqual(duplicated_task.scheduled_for, self.task.scheduled_for)
        self.assertEqual(duplicated_task.weekly_days, self.task.weekly_days)
        self.assertEqual(duplicated_task.fortnight_days, self.task.fortnight_days)
        self.assertEqual(duplicated_task.month_days, self.task.month_days)
        self.assertEqual(
            duplicated_task.monthly_week_days,
            self.task.monthly_week_days,
        )
        self.assertEqual(duplicated_task.position, self.task.position)
        self.assertEqual(duplicated_task.collaborator, self.task.collaborator)
        self.assertEqual(
            duplicated_task.evidence_requirement,
            self.task.evidence_requirement,
        )
        self.assertEqual(duplicated_task.record_format, self.task.record_format)
        self.assertNotEqual(duplicated_task.display_order, self.task.display_order)

        original_rooms = list(self.task.rooms.order_by("pk").values_list("pk", flat=True))
        duplicated_rooms = list(
            duplicated_task.rooms.order_by("pk").values_list("pk", flat=True)
        )
        self.assertEqual(duplicated_rooms, original_rooms)

    def test_duplicate_task_definition_adds_numeric_suffix_when_needed(self):
        with suppress_task_assignment_sync():
            first_response = self.client.post(
                reverse("task_manager:definition-duplicate", args=[self.task.pk])
            )
        new_tasks = TaskDefinition.objects.exclude(pk__in=self.known_task_ids)
        first_copy = (
            new_tasks
            .order_by("-pk")
            .first()
        )
        assert first_copy is not None
        self.known_task_ids.add(first_copy.pk)
        self.assertIn("copia", first_copy.name.lower())
        self.assertEqual(
            first_response["Location"],
            f"{reverse('task_manager:index')}?tm_task={first_copy.pk}#tm-tareas",
        )

        with suppress_task_assignment_sync():
            second_response = self.client.post(
                reverse("task_manager:definition-duplicate", args=[self.task.pk])
            )

        latest_copy = (
            TaskDefinition.objects.exclude(pk__in=self.known_task_ids)
            .order_by("-pk")
            .first()
        )
        assert latest_copy is not None
        self.known_task_ids.add(latest_copy.pk)
        self.assertIn("copia 2", latest_copy.name.lower())
        self.assertEqual(
            second_response["Location"],
            f"{reverse('task_manager:index')}?tm_task={latest_copy.pk}#tm-tareas",
        )


class TaskDefinitionDeleteViewTests(TestCase):
    def setUp(self):
        self.user_model = get_user_model()
        self.staff_user = self.user_model.objects.create_user(
            "901000",
            password="secret-pass",
            nombres="Gestor",
            apellidos="Tareas",
            telefono="3119990000",
            is_staff=True,
        )
        self.client.force_login(self.staff_user)
        self.status = TaskStatus.objects.create(name="Activa")
        self.category = TaskCategory.objects.create(name="Sanidad")

    def _create_task_definition(self, name: str = "Tarea para eliminar") -> TaskDefinition:
        with suppress_task_assignment_sync():
            return TaskDefinition.objects.create(
                name=name,
                status=self.status,
                category=self.category,
            )

    def test_delete_task_removes_assignments(self):
        task = self._create_task_definition()
        collaborator = self.user_model.objects.create_user(
            "901100",
            password=None,
            nombres="Operario",
            apellidos="Demo",
            telefono="3200000000",
        )
        TaskAssignment.objects.create(
            task_definition=task,
            collaborator=collaborator,
            due_date=date(2025, 1, 10),
        )
        TaskAssignment.objects.create(
            task_definition=task,
            due_date=date(2025, 1, 11),
        )

        response = self.client.post(reverse("task_manager:definition-delete", args=[task.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertFalse(TaskDefinition.objects.filter(pk=task.pk).exists())
        self.assertFalse(TaskAssignment.objects.filter(task_definition=task).exists())
        messages = list(get_messages(response.wsgi_request))
        self.assertTrue(any("asignac" in str(message).lower() for message in messages))

    def test_delete_task_without_assignments_shows_simple_message(self):
        task = self._create_task_definition(name="Sin asignaciones")

        response = self.client.post(reverse("task_manager:definition-delete", args=[task.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertFalse(TaskDefinition.objects.filter(pk=task.pk).exists())
        messages = list(get_messages(response.wsgi_request))
        self.assertTrue(messages)
        self.assertNotIn("asignación", str(messages[0]).lower())

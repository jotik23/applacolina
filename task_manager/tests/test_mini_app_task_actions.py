from __future__ import annotations

from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from personal.models import DayOfWeek, PositionCategory, PositionCategoryCode, PositionDefinition, ShiftType
from production.models import ChickenHouse, Farm, Room
from task_manager.models import TaskAssignment, TaskCategory, TaskDefinition, TaskStatus
from task_manager.services import suppress_task_assignment_sync


class MiniAppTaskActionViewTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            "992100",
            password="secret-pass",
            nombres="Operario",
            apellidos="MiniApp",
        )
        access_perm = Permission.objects.get(codename="access_mini_app")
        self.user.user_permissions.add(access_perm)
        self.client.force_login(self.user)

        self.status = TaskStatus.objects.create(name="Activa", is_active=True)
        self.category = TaskCategory.objects.create(name="Sanidad", is_active=True)
        self.farm = Farm.objects.create(name="Granja Pruebas")
        self.house = ChickenHouse.objects.create(farm=self.farm, name="Galpón Uno")
        self.room = Room.objects.create(chicken_house=self.house, name="Sala Demo", area_m2=80)
        position_category, _ = PositionCategory.objects.get_or_create(
            code=PositionCategoryCode.SUPERVISOR,
            defaults={"shift_type": ShiftType.DAY},
        )
        self.position = PositionDefinition.objects.create(
            name="Supervisor Pruebas",
            code="SUP-TEST-001",
            category=position_category,
            farm=self.farm,
            chicken_house=self.house,
            valid_from=date(2024, 1, 1),
        )
        self.position.rooms.add(self.room)
        with suppress_task_assignment_sync():
            self.definition = TaskDefinition.objects.create(
                name="Revisión semanal",
                status=self.status,
                category=self.category,
                collaborator=self.user,
                position=self.position,
                weekly_days=[DayOfWeek.WEDNESDAY],
            )
        self.assignment = TaskAssignment.objects.create(
            task_definition=self.definition,
            collaborator=self.user,
            due_date=timezone.localdate(),
        )

    def test_complete_view_returns_reset_url_and_keeps_card(self):
        url = reverse("task_manager:mini-app-task-complete", args=[self.assignment.pk])
        response = self.client.post(url, data={}, content_type="application/json")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "completed")
        self.assertFalse(payload.get("removed"))
        expected_reset_url = reverse("task_manager:mini-app-task-reset", args=[self.assignment.pk])
        self.assertEqual(payload.get("reset_url"), expected_reset_url)
        self.assignment.refresh_from_db()
        self.assertIsNotNone(self.assignment.completed_on)

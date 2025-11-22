from __future__ import annotations

from django.test import RequestFactory, TestCase
from django.utils import timezone

from personal.models import Role, UserProfile
from task_manager.models import TaskAssignment, TaskCategory, TaskDefinition, TaskStatus
from task_manager.views import build_daily_assignment_report


class TaskManagerDailyReportTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.status = TaskStatus.objects.create(name="Activa", is_active=True)
        self.category = TaskCategory.objects.create(name="General", is_active=True)
        self.user = UserProfile.objects.create_user(
            "99001122",
            password=None,
            nombres="Andrea",
            apellidos="Castro",
            telefono="3200000000",
        )
        self.role = Role.objects.create(name=Role.RoleName.GALPONERO)
        self.user.roles.add(self.role)
        self.today = timezone.localdate()

    def test_report_orders_tasks_by_display_order_and_includes_role_label(self):
        first_task = TaskDefinition.objects.create(
            name="Sanitizar bandejas",
            status=self.status,
            category=self.category,
            display_order=5,
        )
        second_task = TaskDefinition.objects.create(
            name="Evidencia bioseguridad",
            status=self.status,
            category=self.category,
            display_order=25,
        )
        TaskAssignment.objects.create(
            task_definition=second_task,
            collaborator=self.user,
            due_date=self.today,
        )
        TaskAssignment.objects.create(
            task_definition=first_task,
            collaborator=self.user,
            due_date=self.today,
        )

        request = self.factory.get("/task-manager/", {"tm_tab": "reporte"})
        report = build_daily_assignment_report(request, target_date=self.today)

        self.assertTrue(report["role_groups"])
        collaborator = report["role_groups"][0]["collaborators"][0]
        ordered_names = [task["name"] for task in collaborator["tasks"]]
        self.assertEqual(ordered_names, ["Sanitizar bandejas", "Evidencia bioseguridad"])
        self.assertEqual(collaborator["role_label"], self.role.get_name_display())

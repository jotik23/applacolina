from __future__ import annotations

from datetime import date, datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from django.utils.translation import gettext as _

from personal.models import DayOfWeek, PositionCategory, PositionCategoryCode, PositionDefinition, ShiftType
from production.models import ChickenHouse, Farm, Room
from task_manager.models import TaskAssignment, TaskCategory, TaskDefinition, TaskStatus
from task_manager.services import suppress_task_assignment_sync
from task_manager.views import _resolve_daily_task_cards


class MiniAppTaskCardWindowTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            "990010",
            password="secret",
            nombres="Operario",
            apellidos="Pruebas",
        )
        self.status = TaskStatus.objects.create(name="Activa", is_active=True)
        self.category = TaskCategory.objects.create(name="Sanidad", description="Tareas sanitarias", is_active=True)
        self.farm = Farm.objects.create(name="Granja Central")
        self.house = ChickenHouse.objects.create(farm=self.farm, name="Galpón Uno")
        self.room = Room.objects.create(chicken_house=self.house, name="Sala A", area_m2=120)
        self.day_category, _ = PositionCategory.objects.get_or_create(
            code=PositionCategoryCode.SUPERVISOR,
            defaults={"shift_type": ShiftType.DAY},
        )
        if self.day_category.shift_type != ShiftType.DAY:
            self.day_category.shift_type = ShiftType.DAY
            self.day_category.save(update_fields=["shift_type"])
        self.night_category, _ = PositionCategory.objects.get_or_create(
            code=PositionCategoryCode.GALPONERO_PRODUCCION_NOCHE,
            defaults={"shift_type": ShiftType.NIGHT},
        )
        if self.night_category.shift_type != ShiftType.NIGHT:
            self.night_category.shift_type = ShiftType.NIGHT
            self.night_category.save(update_fields=["shift_type"])
        self.day_position = PositionDefinition.objects.create(
            name="Supervisor día",
            code="SUP-DIA-001",
            category=self.day_category,
            farm=self.farm,
            chicken_house=self.house,
            valid_from=date(2024, 1, 1),
        )
        self.day_position.rooms.add(self.room)
        self.night_position = PositionDefinition.objects.create(
            name="Operario noche",
            code="OPE-NOC-001",
            category=self.night_category,
            farm=self.farm,
            chicken_house=self.house,
            valid_from=date(2024, 1, 1),
        )
        self.night_position.rooms.add(self.room)
        self.reference_date = date(2024, 11, 6)

    def _aware_datetime(self, target_date: date, hour: int, minute: int = 0) -> datetime:
        naive = datetime.combine(target_date, time(hour=hour, minute=minute))
        return timezone.make_aware(naive, timezone=timezone.get_current_timezone())

    def _create_assignment(
        self,
        *,
        due_date: date,
        is_accumulative: bool = False,
        position: PositionDefinition | None = None,
        name: str = "Tarea base",
        evidence_requirement: str = TaskDefinition.EvidenceRequirement.NONE,
        task_type: TaskDefinition.TaskType | None = None,
    ) -> TaskAssignment:
        position = position or self.day_position
        with suppress_task_assignment_sync():
            task = TaskDefinition.objects.create(
                name=name,
                description="Tarea de validación",
                status=self.status,
                category=self.category,
                is_mandatory=True,
                is_accumulative=is_accumulative,
                criticality_level=TaskDefinition.CriticalityLevel.MEDIUM,
                task_type=task_type,
                weekly_days=[DayOfWeek.MONDAY],
                position=position,
                evidence_requirement=evidence_requirement,
            )
        return TaskAssignment.objects.create(
            task_definition=task,
            collaborator=self.user,
            due_date=due_date,
        )

    def test_non_accumulative_tasks_skip_previous_days(self):
        self._create_assignment(
            due_date=self.reference_date - timedelta(days=1),
            is_accumulative=False,
            position=self.day_position,
        )

        cards = _resolve_daily_task_cards(
            user=self.user,
            reference_date=self.reference_date,
            current_time=self._aware_datetime(self.reference_date, 10, 0),
        )

        self.assertEqual(cards, [])

    def test_accumulative_tasks_include_previous_days(self):
        assignment = self._create_assignment(
            due_date=self.reference_date - timedelta(days=1),
            is_accumulative=True,
            position=self.day_position,
        )

        cards = _resolve_daily_task_cards(
            user=self.user,
            reference_date=self.reference_date,
            current_time=self._aware_datetime(self.reference_date, 11, 0),
        )

        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["assignment_id"], assignment.pk)

    def test_night_shift_before_cutoff_uses_previous_day(self):
        assignment = self._create_assignment(
            due_date=self.reference_date - timedelta(days=1),
            position=self.night_position,
        )

        cards = _resolve_daily_task_cards(
            user=self.user,
            reference_date=self.reference_date,
            current_time=self._aware_datetime(self.reference_date, 8, 0),
        )

        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["assignment_id"], assignment.pk)
        self.assertNotIn("Retraso", cards[0]["status"]["details"])

    def test_night_shift_after_cutoff_switches_to_current_day(self):
        self._create_assignment(
            due_date=self.reference_date - timedelta(days=1),
            position=self.night_position,
            name="Turno previo",
        )
        assignment_today = self._create_assignment(
            due_date=self.reference_date,
            position=self.night_position,
            name="Turno actual",
        )

        cards = _resolve_daily_task_cards(
            user=self.user,
            reference_date=self.reference_date,
            current_time=self._aware_datetime(self.reference_date, 14, 0),
        )

        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["assignment_id"], assignment_today.pk)

    def test_optional_task_does_not_render_empty_evidence_badge(self):
        assignment = self._create_assignment(
            due_date=self.reference_date,
            position=self.day_position,
        )

        cards = _resolve_daily_task_cards(
            user=self.user,
            reference_date=self.reference_date,
            current_time=self._aware_datetime(self.reference_date, 9, 0),
        )

        self.assertEqual(len(cards), 1)
        card = cards[0]
        badge_labels = [badge["label"] for badge in card.get("badges", [])]
        self.assertNotIn("Sin evidencia adjunta", badge_labels)
        evidence_actions = [action for action in card.get("actions", []) if action.get("action") == "evidence"]
        self.assertEqual(evidence_actions, [])
        self.assertEqual(card["assignment_id"], assignment.pk)

    def test_required_evidence_displays_missing_badge(self):
        assignment = self._create_assignment(
            due_date=self.reference_date,
            position=self.day_position,
            evidence_requirement=TaskDefinition.EvidenceRequirement.PHOTO,
        )

        cards = _resolve_daily_task_cards(
            user=self.user,
            reference_date=self.reference_date,
            current_time=self._aware_datetime(self.reference_date, 9, 0),
        )

        self.assertEqual(len(cards), 1)
        card = cards[0]
        first_badge = card["badges"][0]
        self.assertEqual(first_badge["label"], "Sin evidencia")
        evidence_actions = [action for action in card.get("actions", []) if action.get("action") == "evidence"]
        self.assertTrue(evidence_actions)
        self.assertFalse(evidence_actions[0].get("disabled"))
        self.assertEqual(card["assignment_id"], assignment.pk)

    def test_card_includes_compact_due_label_and_note(self):
        assignment = self._create_assignment(
            due_date=self.reference_date,
            position=self.day_position,
        )

        cards = _resolve_daily_task_cards(
            user=self.user,
            reference_date=self.reference_date,
            current_time=self._aware_datetime(self.reference_date, 6, 0),
        )

        self.assertEqual(len(cards), 1)
        card = cards[0]
        expected_label = _("Hoy")
        self.assertEqual(card["due_compact_label"], expected_label)
        self.assertEqual(card["completion_note"], "")
        self.assertEqual(card["assignment_id"], assignment.pk)

    def test_task_badges_exclude_recurrence_and_priority(self):
        assignment = self._create_assignment(
            due_date=self.reference_date,
            position=self.day_position,
            task_type=TaskDefinition.TaskType.RECURRING,
        )

        cards = _resolve_daily_task_cards(
            user=self.user,
            reference_date=self.reference_date,
            current_time=self._aware_datetime(self.reference_date, 9, 0),
        )

        self.assertEqual(len(cards), 1)
        card = cards[0]
        badge_labels = [badge["label"] for badge in card.get("badges", [])]
        self.assertNotIn(TaskDefinition.TaskType.RECURRING.label, badge_labels)
        self.assertTrue(all("Prioridad" not in label for label in badge_labels))
        self.assertEqual(card["assignment_id"], assignment.pk)

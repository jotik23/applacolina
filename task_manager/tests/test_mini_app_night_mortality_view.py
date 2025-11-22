from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

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
from production.models import (
    BirdBatch,
    BirdBatchRoomAllocation,
    BreedReference,
    ChickenHouse,
    Farm,
    ProductionRecord,
    ProductionRoomRecord,
    Room,
)
from task_manager.mini_app.features.night_mortality import (
    build_night_mortality_registry,
    serialize_night_mortality_registry,
)


class MiniAppNightMortalityViewTests(TestCase):
    def setUp(self):
        self.farm = Farm.objects.create(name="Granja Nocturna")
        self.house_a = ChickenHouse.objects.create(farm=self.farm, name="Galpón A")
        self.house_b = ChickenHouse.objects.create(farm=self.farm, name="Galpón B")
        self.room_a = Room.objects.create(chicken_house=self.house_a, name="Sala 1", area_m2=150)
        self.room_b = Room.objects.create(chicken_house=self.house_b, name="Sala 2", area_m2=130)
        self.breed = BreedReference.objects.create(name="Hy-Line Brown")

        self.category, _ = PositionCategory.objects.get_or_create(
            code=PositionCategoryCode.GALPONERO_PRODUCCION_NOCHE,
            defaults={"shift_type": ShiftType.NIGHT},
        )
        if self.category.shift_type != ShiftType.NIGHT:
            self.category.shift_type = ShiftType.NIGHT
            self.category.save(update_fields=["shift_type"])

        self.position = PositionDefinition.objects.create(
            name="Operario Nocturno",
            code="OPE-NOC-01",
            category=self.category,
            farm=self.farm,
            chicken_house=self.house_a,
            valid_from=timezone.localdate() - timedelta(days=30),
        )
        self.position.rooms.add(self.room_a, self.room_b)

        self.user = UserProfile.objects.create_user(
            username="100200",
            password=None,
            nombres="Ana",
            apellidos="Turnos",
        )
        access_perm = Permission.objects.get(codename="access_mini_app")
        task_perm = Permission.objects.get(codename="view_mini_app_task_cards")
        self.user.user_permissions.add(access_perm, task_perm)

        self.batch = BirdBatch.objects.create(
            farm=self.farm,
            status=BirdBatch.Status.ACTIVE,
            birth_date=timezone.localdate() - timedelta(weeks=35),
            initial_quantity=1200,
            breed=self.breed,
        )
        BirdBatchRoomAllocation.objects.create(bird_batch=self.batch, room=self.room_a, quantity=600)
        BirdBatchRoomAllocation.objects.create(bird_batch=self.batch, room=self.room_b, quantity=580)

        today = timezone.localdate()
        calendar = ShiftCalendar.objects.create(
            name="Calendario Noche",
            start_date=today - timedelta(days=1),
            end_date=today + timedelta(days=1),
            status=CalendarStatus.APPROVED,
        )
        ShiftAssignment.objects.create(
            calendar=calendar,
            position=self.position,
            operator=self.user,
            date=today,
        )
        self.client.force_login(self.user)

    def test_registry_only_available_for_night_shift(self):
        registry = build_night_mortality_registry(user=self.user, reference_date=timezone.localdate())
        self.assertIsNotNone(registry)

        day_category, _ = PositionCategory.objects.get_or_create(
            code=PositionCategoryCode.SUPERVISOR,
            defaults={"shift_type": ShiftType.DAY},
        )
        day_user = UserProfile.objects.create_user(
            username="300400",
            password=None,
            nombres="Carlos",
            apellidos="Dia",
            telefono="3004000",
        )
        access_perm = Permission.objects.get(codename="access_mini_app")
        task_perm = Permission.objects.get(codename="view_mini_app_task_cards")
        day_user.user_permissions.add(access_perm, task_perm)
        day_position = PositionDefinition.objects.create(
            name="Supervisor Día",
            code="SUP-DIA-01",
            category=day_category,
            farm=self.farm,
            chicken_house=self.house_a,
            valid_from=timezone.localdate() - timedelta(days=30),
        )
        day_position.rooms.add(self.room_a)
        calendar = ShiftCalendar.objects.create(
            name="Calendario Día",
            start_date=timezone.localdate() - timedelta(days=10),
            end_date=timezone.localdate() + timedelta(days=10),
            status=CalendarStatus.APPROVED,
        )
        ShiftAssignment.objects.create(
            calendar=calendar,
            position=day_position,
            operator=day_user,
            date=timezone.localdate(),
        )
        self.assertIsNone(build_night_mortality_registry(user=day_user, reference_date=timezone.localdate()))

    def test_registry_serialization_includes_existing_values(self):
        today = timezone.localdate()
        record = ProductionRecord.objects.create(
            bird_batch=self.batch,
            date=today,
            production=Decimal("150.0"),
            consumption=400,
            mortality=5,
            discard=3,
        )
        ProductionRoomRecord.objects.create(
            production_record=record,
            room=self.room_a,
            production=Decimal("70.0"),
            consumption=200,
            mortality=2,
            discard=1,
        )
        ProductionRoomRecord.objects.create(
            production_record=record,
            room=self.room_b,
            production=Decimal("80.0"),
            consumption=200,
            mortality=3,
            discard=2,
        )

        registry = build_night_mortality_registry(user=self.user, reference_date=today)
        self.assertIsNotNone(registry)
        payload = serialize_night_mortality_registry(registry)
        self.assertEqual(payload["total_birds"], 1180)
        lot_payload = payload["lots"][0]
        mortalities = [room["mortality"] for room in lot_payload["rooms"]]
        discards = [room["discard"] for room in lot_payload["rooms"]]
        self.assertIn(2, mortalities)
        self.assertIn(3, mortalities)
        self.assertIn(1, discards)
        self.assertIn(2, discards)

    def test_view_requires_permission(self):
        task_perm = Permission.objects.get(codename="view_mini_app_task_cards")
        self.user.user_permissions.remove(task_perm)
        url = reverse("task_manager:mini-app-night-mortality")
        response = self.client.post(
            url,
            data=json.dumps({"date": timezone.localdate().isoformat(), "lots": []}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)

    def test_view_persists_mortality_updates_records(self):
        today = timezone.localdate()
        url = reverse("task_manager:mini-app-night-mortality")
        payload = {
            "date": today.isoformat(),
            "lots": [
                {
                    "bird_batch": self.batch.pk,
                    "rooms": [
                        {"room_id": self.room_a.pk, "mortality": "4", "discard": "1"},
                        {"room_id": self.room_b.pk, "mortality": "", "discard": "2"},
                    ],
                }
            ],
        }
        response = self.client.post(url, data=json.dumps(payload), content_type="application/json")
        self.assertEqual(response.status_code, 200)
        record = ProductionRecord.objects.get(bird_batch=self.batch, date=today)
        self.assertEqual(record.mortality, 4)
        self.assertEqual(record.discard, 3)
        room_a_record = ProductionRoomRecord.objects.get(production_record=record, room=self.room_a)
        room_b_record = ProductionRoomRecord.objects.get(production_record=record, room=self.room_b)
        self.assertEqual(room_a_record.mortality, 4)
        self.assertEqual(room_b_record.mortality, 0)
        self.assertEqual(room_a_record.discard, 1)
        self.assertEqual(room_b_record.discard, 2)
        data = response.json()
        self.assertIn("night_mortality", data)
        self.assertEqual(data["status"], "ok")

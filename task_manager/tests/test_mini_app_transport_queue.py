from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from production.models import (
    BirdBatch,
    BreedReference,
    ChickenHouse,
    EggClassificationEntry,
    EggClassificationSession,
    EggType,
    Farm,
    ProductionRecord,
    ProductionRoomRecord,
    Room,
)
from personal.models import UserProfile
from task_manager.mini_app.features.transport_queue import build_transport_queue_payload


class MiniAppTransportQueueTests(TestCase):
    def setUp(self) -> None:
        self.origin_farm = Farm.objects.create(name="Granja Origen")
        self.destination_farm = Farm.objects.create(name="Granja Destino")
        self.remote_house = ChickenHouse.objects.create(
            farm=self.origin_farm,
            name="Galpón A",
            egg_destination_farm=self.destination_farm,
        )
        self.remote_room = Room.objects.create(chicken_house=self.remote_house, name="Sala 1", area_m2=120)
        self.local_house = ChickenHouse.objects.create(farm=self.origin_farm, name="Galpón B")
        self.local_room = Room.objects.create(chicken_house=self.local_house, name="Sala 2", area_m2=110)
        self.breed = BreedReference.objects.create(name="Hy-Line Brown")
        self.batch = BirdBatch.objects.create(
            farm=self.origin_farm,
            status=BirdBatch.Status.ACTIVE,
            birth_date=timezone.localdate() - timedelta(weeks=40),
            initial_quantity=1200,
            breed=self.breed,
        )
        self.transporter = UserProfile.objects.create_user(
            cedula="1002003001",
            password=None,
            nombres="Arturo",
            apellidos="Vega",
            telefono="3011112233",
        )
        self.alternate_transporter = UserProfile.objects.create_user(
            cedula="1002003002",
            password=None,
            nombres="Brenda",
            apellidos="Zuluaga",
            telefono="3024445566",
        )

    def _create_production_record(
        self,
        *,
        house: ChickenHouse,
        room: Room,
        date,
        reported_cartons: Decimal,
        received_cartons: Decimal | None,
        classified_cartons: Decimal,
    ) -> ProductionRecord:
        production_eggs = reported_cartons * Decimal("30")
        record = ProductionRecord.objects.create(
            bird_batch=self.batch,
            date=date,
            production=production_eggs,
            consumption=Decimal("0"),
            mortality=0,
            discard=0,
        )
        ProductionRoomRecord.objects.create(
            production_record=record,
            room=room,
            production=production_eggs,
            consumption=Decimal("0"),
            mortality=0,
            discard=0,
        )
        batch = record.egg_classification
        batch.reported_cartons = reported_cartons
        batch.received_cartons = received_cartons
        batch.save(update_fields=["reported_cartons", "received_cartons", "updated_at"])
        session = EggClassificationSession.objects.create(batch=batch)
        EggClassificationEntry.objects.create(
            batch=batch,
            session=session,
            egg_type=EggType.SINGLE_A,
            cartons=classified_cartons,
        )
        return record

    def test_remote_production_with_pending_classification_is_listed(self) -> None:
        target_date = timezone.localdate() - timedelta(days=2)
        record = self._create_production_record(
            house=self.remote_house,
            room=self.remote_room,
            date=target_date,
            reported_cartons=Decimal("30"),
            received_cartons=Decimal("30"),
            classified_cartons=Decimal("10"),
        )

        queue = build_transport_queue_payload()
        self.assertEqual(queue["pending_count"], 1)
        self.assertEqual(queue["total_cartons"], Decimal("30"))
        production_entry = queue["productions"][0]
        self.assertEqual(production_entry["id"], record.pk)
        self.assertEqual(production_entry["cartons"], Decimal("30"))
        self.assertEqual(production_entry["farm"], self.origin_farm.name)
        self.assertEqual(production_entry["rooms"], ["Sala 1"])
        self.assertEqual(
            production_entry["label"],
            f"Lote {self.remote_house.name}, día {target_date.strftime('%d/%m')}",
        )

    def test_completed_classification_is_excluded(self) -> None:
        record = self._create_production_record(
            house=self.remote_house,
            room=self.remote_room,
            date=timezone.localdate() - timedelta(days=3),
            reported_cartons=Decimal("25"),
            received_cartons=Decimal("25"),
            classified_cartons=Decimal("25"),
        )
        # add another session to ensure equality still holds
        batch = record.egg_classification
        session = EggClassificationSession.objects.create(batch=batch)
        EggClassificationEntry.objects.create(
            batch=batch,
            session=session,
            egg_type=EggType.TRIPLE_A,
            cartons=Decimal("0"),
        )

        queue = build_transport_queue_payload()
        self.assertEqual(queue["pending_count"], 0)

    def test_local_destination_is_not_listed(self) -> None:
        self._create_production_record(
            house=self.local_house,
            room=self.local_room,
            date=timezone.localdate() - timedelta(days=1),
            reported_cartons=Decimal("28"),
            received_cartons=Decimal("28"),
            classified_cartons=Decimal("5"),
        )

        queue = build_transport_queue_payload()
        self.assertEqual(queue["pending_count"], 0)

    def test_transporter_options_include_active_users(self) -> None:
        queue = build_transport_queue_payload()
        transporters = queue["transporters"]
        self.assertGreaterEqual(len(transporters), 2)
        labels = [transporter["label"] for transporter in transporters]
        self.assertIn(self.transporter.get_full_name(), labels)
        self.assertIn(self.alternate_transporter.get_full_name(), labels)
        if transporters:
            self.assertEqual(queue["default_transporter_id"], transporters[0]["id"])

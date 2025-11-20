from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from personal.models import UserProfile
from production.models import (
    BirdBatch,
    BreedReference,
    ChickenHouse,
    EggClassificationBatch,
    EggClassificationEntry,
    EggClassificationSession,
    EggType,
    Farm,
    ProductionRecord,
    ProductionRoomRecord,
    Room,
)
from production.services.internal_transport import (
    authorize_internal_transport,
    record_transporter_confirmation,
    record_transport_verification,
)


class InternalTransportServiceTests(TestCase):
    def setUp(self) -> None:
        self.origin_farm = Farm.objects.create(name="Granja Norte")
        self.destination_farm = Farm.objects.create(name="Centro La Colina")
        self.house = ChickenHouse.objects.create(
            farm=self.origin_farm,
            name="Galpón QA",
            egg_destination_farm=self.destination_farm,
        )
        self.room = Room.objects.create(chicken_house=self.house, name="Sala 1", area_m2=120)
        self.breed = BreedReference.objects.create(name="Hy-Line Brown")
        self.batch = BirdBatch.objects.create(
            farm=self.origin_farm,
            status=BirdBatch.Status.ACTIVE,
            birth_date=timezone.localdate() - timedelta(weeks=40),
            initial_quantity=1500,
            breed=self.breed,
        )
        self.transporter = UserProfile.objects.create_user(
            cedula="998877",
            nombres="Coord",
            apellidos="Transporte",
            password=None,
        )

    def _create_production(self) -> EggClassificationBatch:
        production_date = timezone.localdate() - timedelta(days=1)
        record = ProductionRecord.objects.create(
            bird_batch=self.batch,
            date=production_date,
            production=Decimal("900"),
            consumption=Decimal("0"),
            mortality=0,
            discard=0,
        )
        ProductionRoomRecord.objects.create(
            production_record=record,
            room=self.room,
            production=Decimal("900"),
            consumption=Decimal("0"),
            mortality=0,
            discard=0,
        )
        batch = record.egg_classification
        batch.reported_cartons = Decimal("90")
        batch.received_cartons = Decimal("90")
        batch.save(update_fields=["reported_cartons", "received_cartons", "updated_at"])
        session = EggClassificationSession.objects.create(batch=batch)
        EggClassificationEntry.objects.create(
            batch=batch,
            session=session,
            egg_type=EggType.SINGLE_A,
            cartons=Decimal("10"),
        )
        return batch

    def test_authorize_internal_transport_updates_batch(self) -> None:
        batch = self._create_production()
        expected_date = timezone.localdate()

        authorize_internal_transport(
            batch_ids=[batch.pk],
            transporter=self.transporter,
            expected_date=expected_date,
            actor=self.transporter,
        )

        batch.refresh_from_db()
        self.assertEqual(batch.transport_status, EggClassificationBatch.TransportStatus.AUTHORIZED)
        self.assertEqual(batch.transport_transporter, self.transporter)
        self.assertEqual(batch.transport_expected_date, expected_date)
        self.assertEqual(batch.transport_destination_farm, self.destination_farm)

    def test_record_transport_verification_sets_counts(self) -> None:
        batch = self._create_production()
        batch.transport_status = EggClassificationBatch.TransportStatus.VERIFICATION
        batch.save(update_fields=["transport_status"])

        record_transport_verification(
            entries=[{"id": batch.pk, "cartons": Decimal("40")}],
            actor=self.transporter,
        )

        batch.refresh_from_db()
        self.assertEqual(batch.transport_status, EggClassificationBatch.TransportStatus.VERIFIED)
        self.assertEqual(batch.transport_verified_cartons, Decimal("40"))

    def test_record_transporter_confirmation_updates_batch(self) -> None:
        batch = self._create_production()
        expected_date = timezone.localdate()
        authorize_internal_transport(
            batch_ids=[batch.pk],
            transporter=self.transporter,
            expected_date=expected_date,
            actor=self.transporter,
        )

        record_transporter_confirmation(
            entries=[{"id": batch.pk, "cartons": Decimal("55")}],
            actor=self.transporter,
        )

        batch.refresh_from_db()
        self.assertEqual(batch.transport_confirmed_cartons, Decimal("55"))
        self.assertIsNotNone(batch.transport_confirmed_at)
        self.assertEqual(batch.transport_confirmed_by, self.transporter)

    def test_record_transporter_confirmation_rejects_unassigned_user(self) -> None:
        batch = self._create_production()
        expected_date = timezone.localdate()
        authorize_internal_transport(
            batch_ids=[batch.pk],
            transporter=self.transporter,
            expected_date=expected_date,
            actor=self.transporter,
        )
        outsider = UserProfile.objects.create_user(
            cedula="112233",
            nombres="Otro",
            apellidos="Usuario",
            telefono="111222",
            password=None,
        )

        with self.assertRaises(ValidationError):
            record_transporter_confirmation(
                entries=[{"id": batch.pk, "cartons": Decimal("10")}],
                actor=outsider,
            )

    def test_authorize_allows_reset_from_in_transit(self) -> None:
        batch = self._create_production()
        expected_date = timezone.localdate()
        authorize_internal_transport(
            batch_ids=[batch.pk],
            transporter=self.transporter,
            expected_date=expected_date,
            actor=self.transporter,
        )
        # simulate progress to in transit
        batch.transport_status = EggClassificationBatch.TransportStatus.IN_TRANSIT
        batch.save(update_fields=["transport_status"])
        new_transporter = UserProfile.objects.create_user(
            cedula="223344",
            nombres="Nuevo",
            apellidos="Transportador",
            telefono="777888",
            password=None,
        )

        new_date = expected_date + timedelta(days=1)
        authorize_internal_transport(
            batch_ids=[batch.pk],
            transporter=new_transporter,
            expected_date=new_date,
            actor=new_transporter,
        )

        batch.refresh_from_db()
        self.assertEqual(batch.transport_status, EggClassificationBatch.TransportStatus.AUTHORIZED)
        self.assertEqual(batch.transport_transporter, new_transporter)
        self.assertEqual(batch.transport_expected_date, new_date)

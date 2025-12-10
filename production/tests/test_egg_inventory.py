from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from production.models import (
    BirdBatch,
    BreedReference,
    EggClassificationEntry,
    EggDispatch,
    EggDispatchDestination,
    EggDispatchItem,
    EggType,
    Farm,
    ProductionRecord,
)
from production.services.egg_classification import record_classification_results, summarize_classified_inventory


class EggInventoryDashboardTests(TestCase):
    def setUp(self) -> None:
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            cedula="inventory-admin",
            password="strongpass",
            nombres="Inventory",
            apellidos="Admin",
            telefono="3000002000",
            is_staff=True,
        )
        self.client.force_login(self.user)
        self.farm = Farm.objects.create(name="Central")
        self.breed = BreedReference.objects.create(name="Hy-Line")
        self.batch = BirdBatch.objects.create(
            farm=self.farm,
            status=BirdBatch.Status.ACTIVE,
            birth_date=date.today(),
            initial_quantity=1200,
            breed=self.breed,
        )
        self.record = ProductionRecord.objects.create(
            bird_batch=self.batch,
            date=date.today(),
            production=Decimal("150.0"),
            consumption=Decimal("100"),
            mortality=3,
            discard=1,
        )

    def test_dashboard_renders_pending_batches(self) -> None:
        response = self.client.get(reverse("home:egg-inventory"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "production/egg_inventory.html")
        self.assertIn("pending_batches", response.context)
        self.assertTrue(response.context["pending_batches"])

    def test_confirm_receipt_updates_batch(self) -> None:
        batch = self.record.egg_classification
        response = self.client.post(
            reverse("production:egg-inventory-batch", args=[batch.pk]),
            {
                "form": "receipt",
                "batch_id": batch.pk,
                "received_cartons": "148.5",
                "notes": "Diferencia menor.",
            },
        )
        self.assertRedirects(response, reverse("production:egg-inventory-batch", args=[batch.pk]))
        batch.refresh_from_db()
        self.assertEqual(batch.received_cartons, Decimal("148.5"))
        self.assertEqual(batch.notes, "Diferencia menor.")
        self.assertEqual(batch.status, batch.Status.CONFIRMED)

    def test_classification_requires_confirmation(self) -> None:
        batch = self.record.egg_classification
        response = self.client.post(
            reverse("production:egg-inventory-batch", args=[batch.pk]),
            {
                "form": "classification",
                "batch_id": batch.pk,
                "type_jumbo": "80",
                "type_aaa": "70",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["classification_form"].errors or response.context["classification_form"].non_field_errors())
        self.assertFalse(EggClassificationEntry.objects.exists())

    def test_classification_flow_creates_entries(self) -> None:
        batch = self.record.egg_classification
        self.client.post(
            reverse("production:egg-inventory-batch", args=[batch.pk]),
            {
                "form": "receipt",
                "batch_id": batch.pk,
                "received_cartons": "150.0",
            },
        )
        response = self.client.post(
            reverse("production:egg-inventory-batch", args=[batch.pk]),
            {
                "form": "classification",
                "batch_id": batch.pk,
                "type_jumbo": "50",
                "type_aaa": "50",
                "type_aa": "50",
            },
        )
        self.assertRedirects(response, reverse("production:egg-inventory-batch", args=[batch.pk]))
        batch.refresh_from_db()
        entries = EggClassificationEntry.objects.filter(batch=batch)
        self.assertEqual(entries.count(), 3)
        self.assertEqual(sum(entry.cartons for entry in entries), Decimal("150"))
        self.assertEqual(batch.status, batch.Status.CLASSIFIED)
        self.assertEqual(batch.classified_total, Decimal("150"))
        self.assertEqual(batch.classification_sessions.count(), 1)

    def test_classification_allows_partial_totals(self) -> None:
        batch = self.record.egg_classification
        self.client.post(
            reverse("production:egg-inventory-batch", args=[batch.pk]),
            {
                "form": "receipt",
                "batch_id": batch.pk,
                "received_cartons": "150.0",
            },
        )
        response = self.client.post(
            reverse("production:egg-inventory-batch", args=[batch.pk]),
            {
                "form": "classification",
                "batch_id": batch.pk,
                "type_jumbo": "60",
                "type_aaa": "40",
            },
            follow=True,
        )
        self.assertRedirects(response, reverse("production:egg-inventory-batch", args=[batch.pk]))
        batch.refresh_from_db()
        self.assertEqual(batch.classified_total, Decimal("100"))
        self.assertEqual(batch.status, batch.Status.CONFIRMED)

        second_response = self.client.post(
            reverse("production:egg-inventory-batch", args=[batch.pk]),
            {
                "form": "classification",
                "batch_id": batch.pk,
                "type_aa": "50",
            },
        )
        self.assertRedirects(second_response, reverse("production:egg-inventory-batch", args=[batch.pk]))
        batch.refresh_from_db()
        self.assertEqual(batch.classified_total, Decimal("150"))
        self.assertEqual(batch.status, batch.Status.CLASSIFIED)
        self.assertEqual(batch.classification_sessions.count(), 2)

    def test_classification_rejects_totals_above_received(self) -> None:
        batch = self.record.egg_classification
        self.client.post(
            reverse("production:egg-inventory-batch", args=[batch.pk]),
            {
                "form": "receipt",
                "batch_id": batch.pk,
                "received_cartons": "150.0",
            },
        )
        response = self.client.post(
            reverse("production:egg-inventory-batch", args=[batch.pk]),
            {
                "form": "classification",
                "batch_id": batch.pk,
                "type_jumbo": "120",
                "type_aaa": "40",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["classification_form"].non_field_errors())
        batch.refresh_from_db()
        self.assertEqual(batch.classification_entries.count(), 0)
        self.assertEqual(batch.classification_sessions.count(), 0)

    def test_service_rejects_zero_entries(self) -> None:
        batch = self.record.egg_classification
        batch.received_cartons = Decimal("100")
        batch.save(update_fields=["received_cartons"])
        with self.assertRaises(ValueError):
            record_classification_results(batch=batch, entries={"jumbo": Decimal("0")}, actor_id=self.user.id)
        self.assertEqual(batch.classification_sessions.count(), 0)

    def test_cardex_view_filters_by_month_and_farm(self) -> None:
        batch = self.record.egg_classification
        batch.received_cartons = Decimal("150")
        batch.save(update_fields=["received_cartons"])
        record_classification_results(
            batch=batch,
            entries={"jumbo": Decimal("80"), "aaa": Decimal("70")},
            actor_id=self.user.id,
        )

        other_farm = Farm.objects.create(name="Auxiliar")
        other_batch = BirdBatch.objects.create(
            farm=other_farm,
            status=BirdBatch.Status.ACTIVE,
            birth_date=date.today(),
            initial_quantity=800,
            breed=self.breed,
        )
        other_record = ProductionRecord.objects.create(
            bird_batch=other_batch,
            date=date.today(),
            production=Decimal("100"),
            consumption=Decimal("80"),
            mortality=1,
            discard=0,
        )
        other_classification = other_record.egg_classification
        other_classification.received_cartons = Decimal("90")
        other_classification.save(update_fields=["received_cartons"])
        record_classification_results(
            batch=other_classification,
            entries={"aa": Decimal("90")},
            actor_id=self.user.id,
        )

        dispatch = EggDispatch.objects.create(
            date=date.today(),
            destination=EggDispatchDestination.TIERRALTA,
            driver=self.user,
            seller=self.user,
            total_cartons=Decimal("40"),
        )
        EggDispatchItem.objects.create(dispatch=dispatch, egg_type=EggType.JUMBO, cartons=Decimal("40"))

        url = reverse("production:egg-inventory-cardex")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "production/egg_inventory_cardex.html")
        flows = response.context["flows"]
        self.assertTrue(flows)
        self.assertLessEqual(flows[0].day, date.today())
        self.assertTrue(any(record.last_classified_at for flow in flows for record in flow.records))

        dispatch_day_map = response.context["dispatch_day_map"]
        self.assertIn(dispatch.date, dispatch_day_map)
        dispatch_summary = dispatch_day_map[dispatch.date]
        self.assertEqual(dispatch_summary.dispatches[0].total_cartons, Decimal("40"))
        self.assertTrue(response.context["inventory_argument_rows"])
        self.assertEqual(
            response.context["inventory_argument_totals"]["dispatched"],
            Decimal("40"),
        )
        breakdown_columns = response.context["classification_breakdown_columns"]
        self.assertGreaterEqual(len(breakdown_columns), 2)
        first_row = response.context["inventory_argument_rows"][0]
        self.assertIn("farm_breakdown", first_row)
        self.assertTrue(
            all(column["id"] in first_row["farm_breakdown"] for column in breakdown_columns)
        )

        filtered_response = self.client.get(url, {"farm": other_farm.pk})
        self.assertEqual(filtered_response.status_code, 200)
        filtered_flows = filtered_response.context["flows"]
        self.assertTrue(
            all(
                all(record.farm_name == other_farm.name for record in flow.records)
                for flow in filtered_flows
            )
        )
        filtered_columns = filtered_response.context["classification_breakdown_columns"]
        self.assertEqual(len(filtered_columns), 1)
        self.assertEqual(filtered_columns[0]["id"], other_farm.pk)

    def test_cardex_argument_includes_previous_balance(self) -> None:
        target_month = self.record.date.replace(day=1)
        previous_day = target_month - timedelta(days=1)

        # Classification recorded before the current month to seed the starting balance.
        previous_farm = Farm.objects.create(name="Histórica")
        previous_batch = BirdBatch.objects.create(
            farm=previous_farm,
            status=BirdBatch.Status.ACTIVE,
            birth_date=date.today(),
            initial_quantity=900,
            breed=self.breed,
        )
        previous_record = ProductionRecord.objects.create(
            bird_batch=previous_batch,
            date=previous_day,
            production=Decimal("120"),
            consumption=Decimal("100"),
            mortality=2,
            discard=0,
        )
        previous_classification = previous_record.egg_classification
        previous_classification.received_cartons = Decimal("100")
        previous_classification.save(update_fields=["received_cartons"])
        record_classification_results(
            batch=previous_classification,
            entries={"jumbo": Decimal("100")},
            actor_id=self.user.id,
        )
        session = previous_classification.classification_sessions.first()
        if session:
            session.classified_at = timezone.make_aware(datetime.combine(previous_day, datetime.min.time()))
            session.save(update_fields=["classified_at"])

        previous_dispatch = EggDispatch.objects.create(
            date=previous_day,
            destination=EggDispatchDestination.TIERRALTA,
            driver=self.user,
            seller=self.user,
            total_cartons=Decimal("20"),
        )
        EggDispatchItem.objects.create(
            dispatch=previous_dispatch,
            egg_type=EggType.JUMBO,
            cartons=Decimal("20"),
        )

        # Current month classification to keep the flow active.
        batch = self.record.egg_classification
        batch.received_cartons = Decimal("150")
        batch.save(update_fields=["received_cartons"])
        record_classification_results(
            batch=batch,
            entries={"jumbo": Decimal("80"), "aaa": Decimal("70")},
            actor_id=self.user.id,
        )

        url = reverse("production:egg-inventory-cardex")
        response = self.client.get(url, {"month": target_month.strftime("%Y-%m")})
        self.assertEqual(response.status_code, 200)
        rows = response.context["inventory_argument_rows"]
        jumbo_row = next(row for row in rows if row["egg_type"] == EggType.JUMBO)
        # The previous month balance is 100 classified - 20 dispatched = 80 cartones.
        self.assertEqual(jumbo_row["starting"], Decimal("80"))
        totals = response.context["inventory_argument_totals"]
        self.assertEqual(totals["starting"], Decimal("80"))

    def test_cardex_argument_uses_session_date_for_month_totals(self) -> None:
        target_month = date(2024, 9, 1)
        next_month = (target_month.replace(day=28) + timedelta(days=4)).replace(day=1)

        month_record = ProductionRecord.objects.create(
            bird_batch=self.batch,
            date=target_month,
            production=Decimal("120"),
            consumption=Decimal("90"),
            mortality=0,
            discard=0,
        )
        batch = month_record.egg_classification
        batch.received_cartons = Decimal("100")
        batch.save(update_fields=["received_cartons"])
        record_classification_results(
            batch=batch,
            entries={"aa": Decimal("100")},
            actor_id=self.user.id,
        )
        session = batch.classification_sessions.first()
        assert session is not None
        session.classified_at = timezone.make_aware(
            datetime.combine(next_month, datetime.min.time())
        )
        session.save(update_fields=["classified_at"])

        url = reverse("production:egg-inventory-cardex")
        september_response = self.client.get(url, {"month": target_month.strftime("%Y-%m")})
        self.assertEqual(september_response.status_code, 200)
        september_rows = september_response.context["inventory_argument_rows"]
        aa_row = next(row for row in september_rows if row["egg_type"] == EggType.DOUBLE_A)
        self.assertEqual(aa_row["classified"], Decimal("0"))

        october_response = self.client.get(url, {"month": next_month.strftime("%Y-%m")})
        self.assertEqual(october_response.status_code, 200)
        october_rows = october_response.context["inventory_argument_rows"]
        october_aa_row = next(row for row in october_rows if row["egg_type"] == EggType.DOUBLE_A)
        self.assertEqual(october_aa_row["classified"], Decimal("100"))

    def test_cardex_sessions_view_lists_partial_rows(self) -> None:
        batch = self.record.egg_classification
        batch.received_cartons = Decimal("150")
        batch.save(update_fields=["received_cartons"])
        record_classification_results(
            batch=batch,
            entries={"jumbo": Decimal("80"), "aaa": Decimal("70")},
            actor_id=self.user.id,
        )
        url = reverse("production:egg-inventory-cardex")
        response = self.client.get(url, {"view": "sessions"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["cardex_view"], "sessions")
        session_flows = response.context["session_flows"]
        self.assertTrue(session_flows)
        first_day = session_flows[0]
        self.assertTrue(first_day.sessions)
        first_session = first_day.sessions[0]
        self.assertEqual(first_session.batch_id, batch.pk)
        self.assertEqual(first_session.session_cartons, Decimal("150"))
        self.assertEqual(first_session.produced_cartons, batch.reported_cartons)

    def test_can_delete_classification_session(self) -> None:
        batch = self.record.egg_classification
        self.client.post(
            reverse("production:egg-inventory-batch", args=[batch.pk]),
            {
                "form": "receipt",
                "batch_id": batch.pk,
                "received_cartons": "120.0",
            },
        )
        self.client.post(
            reverse("production:egg-inventory-batch", args=[batch.pk]),
            {
                "form": "classification",
                "batch_id": batch.pk,
                "type_jumbo": "60",
            },
        )
        session = batch.classification_sessions.first()
        response = self.client.post(
            reverse("production:egg-inventory-batch", args=[batch.pk]),
            {
                "form": "delete_session",
                "session_id": session.pk if session else "",
            },
        )
        self.assertRedirects(response, reverse("production:egg-inventory-batch", args=[batch.pk]))
        batch.refresh_from_db()
        self.assertEqual(batch.classification_sessions.count(), 0)

    def test_can_update_classification_session_date(self) -> None:
        batch = self.record.egg_classification
        batch.received_cartons = Decimal("120")
        batch.save(update_fields=["received_cartons"])
        record_classification_results(
            batch=batch,
            entries={"jumbo": Decimal("60"), "aaa": Decimal("60")},
            actor_id=self.user.id,
        )
        session = batch.classification_sessions.first()
        assert session is not None
        target_date = date.today() - timedelta(days=2)
        url = reverse("production:egg-classification-session-date", args=[session.pk])
        response = self.client.post(
            url,
            {"classified_date": target_date.strftime("%Y-%m-%d")},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["session_id"], session.pk)
        session.refresh_from_db()
        self.assertEqual(timezone.localtime(session.classified_at).date(), target_date)
        batch.refresh_from_db()
        self.assertEqual(timezone.localtime(batch.classified_at).date(), target_date)
        self.assertEqual(batch.classified_total, Decimal("120"))
        self.assertEqual(batch.status, batch.Status.CLASSIFIED)

    def test_can_reset_batch_progress(self) -> None:
        batch = self.record.egg_classification
        self.client.post(
            reverse("production:egg-inventory-batch", args=[batch.pk]),
            {
                "form": "receipt",
                "batch_id": batch.pk,
                "received_cartons": "140.0",
                "notes": "Recepción inicial",
            },
        )
        self.client.post(
            reverse("production:egg-inventory-batch", args=[batch.pk]),
            {
                "form": "classification",
                "batch_id": batch.pk,
                "type_jumbo": "40",
                "type_aaa": "30",
            },
        )
        response = self.client.post(
            reverse("production:egg-inventory-batch", args=[batch.pk]),
            {
                "form": "reset_batch",
            },
        )
        self.assertRedirects(response, reverse("production:egg-inventory-batch", args=[batch.pk]))
        batch.refresh_from_db()
        self.assertEqual(batch.received_cartons, Decimal("0"))
        self.assertEqual(batch.status, batch.Status.PENDING)
        self.assertEqual(batch.classification_sessions.count(), 0)
        self.assertEqual(batch.classified_total, Decimal("0"))


class EggDispatchViewsTests(TestCase):
    def setUp(self) -> None:
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            cedula="dispatch-admin",
            password="strongpass",
            nombres="Logistica",
            apellidos="Admin",
            telefono="3000002010",
            is_staff=True,
        )
        self.client.force_login(self.user)
        self.farm = Farm.objects.create(name="Central")
        self.breed = BreedReference.objects.create(name="Hy-Line")
        self.batch = BirdBatch.objects.create(
            farm=self.farm,
            status=BirdBatch.Status.ACTIVE,
            birth_date=date.today(),
            initial_quantity=1200,
            breed=self.breed,
        )
        self.record = ProductionRecord.objects.create(
            bird_batch=self.batch,
            date=date.today(),
            production=Decimal("200"),
            consumption=Decimal("120"),
            mortality=2,
            discard=0,
        )
        self._seed_inventory()

    def _seed_inventory(self) -> None:
        batch = self.record.egg_classification
        batch.received_cartons = Decimal("200")
        batch.save(update_fields=["received_cartons"])
        record_classification_results(
            batch=batch,
            entries={"jumbo": Decimal("120"), "aaa": Decimal("50"), "aa": Decimal("30")},
            actor_id=self.user.id,
        )

    def test_dispatch_creation_consumes_inventory(self) -> None:
        response = self.client.post(
            reverse("administration:egg-dispatch-create"),
            {
                "date": date.today().isoformat(),
                "destination": EggDispatchDestination.TIERRALTA,
                "driver": self.user.pk,
                "seller": self.user.pk,
                "notes": "Ruta a Tierralta",
                "type_jumbo": "60",
                "type_aaa": "30",
            },
        )
        self.assertRedirects(response, reverse("administration:egg-dispatch-list"))
        dispatch = EggDispatch.objects.get()
        self.assertEqual(dispatch.total_cartons, Decimal("90"))
        self.assertEqual(dispatch.items.count(), 2)
        inventory_rows = summarize_classified_inventory()
        jumbo_row = next(row for row in inventory_rows if row.egg_type == EggType.JUMBO)
        self.assertEqual(jumbo_row.cartons, Decimal("60"))

    def test_dispatch_allows_exceeding_inventory(self) -> None:
        response = self.client.post(
            reverse("administration:egg-dispatch-create"),
            {
                "date": date.today().isoformat(),
                "destination": EggDispatchDestination.MONTERIA,
                "driver": self.user.pk,
                "seller": self.user.pk,
                "type_jumbo": "999",
            },
        )
        self.assertRedirects(response, reverse("administration:egg-dispatch-list"))
        dispatch = EggDispatch.objects.get()
        self.assertEqual(dispatch.total_cartons, Decimal("999"))
        jumbo_balance = next(
            row.cartons for row in summarize_classified_inventory() if row.egg_type == EggType.JUMBO
        )
        self.assertEqual(jumbo_balance, Decimal("120") - Decimal("999"))

    def test_dispatch_update_replaces_items(self) -> None:
        dispatch = EggDispatch.objects.create(
            date=date.today(),
            destination=EggDispatchDestination.TIERRALTA,
            driver=self.user,
            seller=self.user,
            notes="Inicial",
            total_cartons=Decimal("40"),
            created_by=self.user,
        )
        dispatch.items.create(egg_type=EggType.JUMBO, cartons=Decimal("40"))
        response = self.client.post(
            reverse("administration:egg-dispatch-update", args=[dispatch.pk]),
            {
                "date": date.today().isoformat(),
                "destination": EggDispatchDestination.BAJO_CAUCA,
                "driver": self.user.pk,
                "seller": self.user.pk,
                "type_jumbo": "20",
                "type_aa": "20",
            },
        )
        self.assertRedirects(response, reverse("administration:egg-dispatch-list"))
        dispatch.refresh_from_db()
        self.assertEqual(dispatch.total_cartons, Decimal("40"))
        items = {
            (item.egg_type, item.cartons)
            for item in dispatch.items.order_by("egg_type")
        }
        self.assertEqual(
            items,
            {
                (EggType.JUMBO, Decimal("20")),
                (EggType.DOUBLE_A, Decimal("20")),
            },
        )

    def test_dispatch_list_view_includes_records(self) -> None:
        EggDispatch.objects.create(
            date=date.today(),
            destination=EggDispatchDestination.MONTERIA,
            driver=self.user,
            seller=self.user,
            total_cartons=Decimal("10"),
            created_by=self.user,
        )
        response = self.client.get(reverse("administration:egg-dispatch-list"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("dispatches", response.context)
        self.assertTrue(response.context["dispatches"])

    def test_only_staff_can_access_dispatch_views(self) -> None:
        self.client.logout()
        user_model = get_user_model()
        regular = user_model.objects.create_user(
            cedula="regular-user",
            password="strongpass",
            nombres="Regular",
            apellidos="User",
            telefono="3000004000",
            is_staff=False,
        )
        permission = Permission.objects.get(codename="access_egg_inventory")
        regular.user_permissions.add(permission)
        self.client.force_login(regular)
        response = self.client.get(reverse("administration:egg-dispatch-list"))
        self.assertRedirects(response, reverse("task_manager:telegram-mini-app"))

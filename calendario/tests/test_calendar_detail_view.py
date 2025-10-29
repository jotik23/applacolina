from __future__ import annotations

from datetime import date, timedelta

from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from calendario.models import (
    AssignmentAlertLevel,
    CalendarStatus,
    OperatorRestPeriod,
    PositionCategory,
    PositionCategoryCode,
    PositionDefinition,
    RestPeriodSource,
    RestPeriodStatus,
    ShiftAssignment,
    ShiftCalendar,
    ShiftType,
    WorkloadSnapshot,
)
REST_CELL_STATE_REST = "rest"
REST_CELL_STATE_UNASSIGNED = "unassigned"
from granjas.models import Farm
from users.models import UserProfile


class CalendarDetailViewManualOverrideTests(TestCase):
    def setUp(self) -> None:
        self.user = UserProfile.objects.create_user(
            cedula="1000",
            password="test",  # noqa: S106 - test credential
            nombres="Coordinador",
            apellidos="Calendario",
            telefono="3100000000",
        )
        self.client.force_login(self.user)

        self.farm = Farm.objects.create(name="Colina")

        self.category, _created = PositionCategory.objects.get_or_create(
            code=PositionCategoryCode.GALPONERO_PRODUCCION_DIA,
            defaults={
                "shift_type": ShiftType.DAY,
                "rest_max_consecutive_days": 8,
                "rest_post_shift_days": 0,
                "rest_monthly_days": 5,
            },
        )

        self.category.rest_max_consecutive_days = 8
        self.category.rest_post_shift_days = 0
        self.category.rest_monthly_days = 5
        self.category.save()

        self.calendar = ShiftCalendar.objects.create(
            name="Semana 27",
            start_date=date(2025, 7, 7),
            end_date=date(2025, 7, 13),
            status=CalendarStatus.DRAFT,
            created_by=self.user,
        )

        self.position_primary = PositionDefinition.objects.create(
            name="Galponero día A",
            code="POS-A",
            category=self.category,
            farm=self.farm,
            valid_from=self.calendar.start_date,
            valid_until=self.calendar.end_date,
        )
        self.position_secondary = PositionDefinition.objects.create(
            name="Galponero día B",
            code="POS-B",
            category=self.category,
            farm=self.farm,
            valid_from=self.calendar.start_date,
            valid_until=self.calendar.end_date,
        )

        self.operator_initial = UserProfile.objects.create_user(
            cedula="2000",
            password="test",  # noqa: S106 - test credential
            nombres="Operario",
            apellidos="Inicial",
            telefono="3100000001",
        )
        self.operator_conflict = UserProfile.objects.create_user(
            cedula="2001",
            password="test",  # noqa: S106 - test credential
            nombres="Operario",
            apellidos="Conflicto",
            telefono="3100000002",
        )
        self.operator_manual = UserProfile.objects.create_user(
            cedula="2002",
            password="test",  # noqa: S106 - test credential
            nombres="Operario",
            apellidos="Manual",
            telefono="3100000003",
        )

        self.operator_initial.suggested_positions.add(self.position_primary)
        self.operator_conflict.suggested_positions.add(
            self.position_primary,
            self.position_secondary,
        )

        self.assignment_primary = ShiftAssignment.objects.create(
            calendar=self.calendar,
            position=self.position_primary,
            date=self.calendar.start_date,
            operator=self.operator_initial,
            alert_level=AssignmentAlertLevel.NONE,
            is_auto_assigned=True,
        )
        self.assignment_conflict = ShiftAssignment.objects.create(
            calendar=self.calendar,
            position=self.position_secondary,
            date=self.calendar.start_date,
            operator=self.operator_conflict,
            alert_level=AssignmentAlertLevel.NONE,
            is_auto_assigned=True,
        )

    def test_get_includes_rest_summary_information(self) -> None:
        last_rest_start = self.calendar.start_date - timedelta(days=3)
        last_rest_end = self.calendar.start_date - timedelta(days=1)
        next_rest_start = self.calendar.end_date + timedelta(days=1)
        next_rest_end = self.calendar.end_date + timedelta(days=3)

        self.operator_initial.employment_start_date = self.calendar.start_date - timedelta(days=60)
        self.operator_initial.save(update_fields=["employment_start_date"])

        OperatorRestPeriod.objects.create(
            operator=self.operator_initial,
            start_date=last_rest_start,
            end_date=last_rest_end,
            status=RestPeriodStatus.CONFIRMED,
            source=RestPeriodSource.MANUAL,
        )
        OperatorRestPeriod.objects.create(
            operator=self.operator_initial,
            start_date=next_rest_start,
            end_date=next_rest_end,
            status=RestPeriodStatus.APPROVED,
            source=RestPeriodSource.MANUAL,
        )

        url = reverse("calendario:calendar-detail", args=[self.calendar.pk])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertIn("rest_rows", response.context)
        self.assertIn("rest_summary", response.context)

        summary = response.context["rest_summary"]
        key = str(self.operator_initial.pk)
        self.assertIn(key, summary)
        operator_summary = summary[key]
        self.assertEqual(operator_summary["employment_start"], (self.calendar.start_date - timedelta(days=60)).isoformat())
        self.assertIsNotNone(operator_summary["recent"])
        self.assertEqual(operator_summary["recent"]["start"], last_rest_start.isoformat())
        self.assertIsNotNone(operator_summary["upcoming"])
        self.assertEqual(operator_summary["upcoming"]["start"], next_rest_start.isoformat())

    def test_update_assignment_resets_conflicting_assignment(self) -> None:
        url = reverse("calendario:calendar-detail", args=[self.calendar.pk])
        response = self.client.post(
            url,
            data={
                "action": "update-assignment",
                "assignment_id": self.assignment_primary.pk,
                "operator_id": self.operator_conflict.pk,
                "force_override": "1",
            },
        )

        self.assertRedirects(response, url)

        self.assignment_primary.refresh_from_db()
        self.assertEqual(self.assignment_primary.operator, self.operator_conflict)
        self.assertEqual(self.assignment_primary.alert_level, AssignmentAlertLevel.NONE)
        self.assertFalse(self.assignment_primary.is_overtime)
        self.assertEqual(self.assignment_primary.overtime_points, 0)
        self.assertFalse(self.assignment_primary.is_auto_assigned)

        conflict_exists = ShiftAssignment.objects.filter(pk=self.assignment_conflict.pk).exists()
        self.assertFalse(conflict_exists)

        messages = list(get_messages(response.wsgi_request))
        self.assertTrue(any("Se liberó el turno previo" in message.message for message in messages))

        detail_response = self.client.get(url)
        self.assertEqual(detail_response.status_code, 200)
        rows = detail_response.context["rows"]
        primary_row = next(
            (row for row in rows if row["position"].pk == self.position_primary.pk),
            None,
        )
        self.assertIsNotNone(primary_row)
        primary_cell = next(
            (cell for cell in primary_row["cells"] if cell["date"] == self.calendar.start_date),
            None,
        )
        self.assertIsNotNone(primary_cell)
        self.assertIsNone(primary_cell["overtime_message"])
        self.assertEqual(primary_cell["operator"]["id"], self.operator_conflict.pk)

    def test_update_assignment_removes_overlapping_rest_period(self) -> None:
        rest_period = OperatorRestPeriod.objects.create(
            operator=self.operator_manual,
            start_date=self.calendar.start_date,
            end_date=self.calendar.start_date,
            status=RestPeriodStatus.APPROVED,
            source=RestPeriodSource.MANUAL,
            calendar=self.calendar,
        )

        url = reverse("calendario:calendar-detail", args=[self.calendar.pk])
        response = self.client.post(
            url,
            data={
                "action": "update-assignment",
                "assignment_id": self.assignment_primary.pk,
                "operator_id": self.operator_manual.pk,
                "force_override": "1",
            },
        )

        self.assertRedirects(response, url)

        with self.assertRaises(OperatorRestPeriod.DoesNotExist):
            rest_period.refresh_from_db()

        messages = list(get_messages(response.wsgi_request))
        self.assertTrue(
            any("Se eliminó 1 descanso" in message.message for message in messages)
        )

        self.assignment_primary.refresh_from_db()
        self.assertEqual(self.assignment_primary.operator, self.operator_manual)

    def test_create_assignment_accepts_manual_override_without_suggestion(self) -> None:
        url = reverse("calendario:calendar-detail", args=[self.calendar.pk])
        target_date = self.calendar.start_date + timedelta(days=1)

        response = self.client.post(
            url,
            data={
                "action": "create-assignment",
                "position_id": self.position_secondary.pk,
                "date": target_date.isoformat(),
                "operator_id": self.operator_manual.pk,
                "force_override": "1",
            },
        )

        self.assertRedirects(response, url)

        created_assignment = ShiftAssignment.objects.get(
            calendar=self.calendar,
            position=self.position_secondary,
            date=target_date,
        )

        self.assertEqual(created_assignment.operator, self.operator_manual)
        self.assertEqual(created_assignment.alert_level, AssignmentAlertLevel.WARN)
        self.assertFalse(created_assignment.is_overtime)
        self.assertEqual(created_assignment.overtime_points, 0)
        self.assertFalse(created_assignment.is_auto_assigned)

        messages = list(get_messages(response.wsgi_request))
        self.assertTrue(
            any("Turno asignado manualmente." in message.message for message in messages)
        )

        detail_response = self.client.get(url)
        self.assertEqual(detail_response.status_code, 200)
        rows = detail_response.context["rows"]
        matching_row = next(
            (row for row in rows if row["position"].pk == self.position_secondary.pk),
            None,
        )
        self.assertIsNotNone(matching_row)
        matching_cell = next(
            (cell for cell in matching_row["cells"] if cell["date"] == target_date),
            None,
        )
        self.assertIsNotNone(matching_cell)
        self.assertEqual(matching_cell["alert"], AssignmentAlertLevel.WARN.value)
        self.assertEqual(
            matching_cell["skill_gap_message"],
            "Operario sin sugerencia registrada. Asignación manual confirmada.",
        )

    def test_create_assignment_resets_conflicting_assignment(self) -> None:
        url = reverse("calendario:calendar-detail", args=[self.calendar.pk])
        target_date = self.calendar.start_date + timedelta(days=1)

        existing_assignment = ShiftAssignment.objects.create(
            calendar=self.calendar,
            position=self.position_primary,
            date=target_date,
            operator=self.operator_conflict,
            alert_level=AssignmentAlertLevel.NONE,
            is_auto_assigned=True,
        )

        response = self.client.post(
            url,
            data={
                "action": "create-assignment",
                "position_id": self.position_secondary.pk,
                "date": target_date.isoformat(),
                "operator_id": self.operator_conflict.pk,
            },
        )

        self.assertRedirects(response, url)
        self.assertFalse(ShiftAssignment.objects.filter(pk=existing_assignment.pk).exists())

        created_assignment = ShiftAssignment.objects.get(
            calendar=self.calendar,
            position=self.position_secondary,
            date=target_date,
        )
        self.assertEqual(created_assignment.operator, self.operator_conflict)
        self.assertFalse(created_assignment.is_auto_assigned)

        messages = list(get_messages(response.wsgi_request))
        self.assertTrue(any("Se liberó el turno previo" in message.message for message in messages))

    def test_create_assignment_removes_overlapping_rest_period(self) -> None:
        url = reverse("calendario:calendar-detail", args=[self.calendar.pk])
        target_date = self.calendar.start_date + timedelta(days=1)

        rest_period = OperatorRestPeriod.objects.create(
            operator=self.operator_manual,
            start_date=target_date,
            end_date=target_date,
            status=RestPeriodStatus.APPROVED,
            source=RestPeriodSource.MANUAL,
        )

        response = self.client.post(
            url,
            data={
                "action": "create-assignment",
                "position_id": self.position_secondary.pk,
                "date": target_date.isoformat(),
                "operator_id": self.operator_manual.pk,
            },
        )

        self.assertRedirects(response, url)
        with self.assertRaises(OperatorRestPeriod.DoesNotExist):
            rest_period.refresh_from_db()

        messages = list(get_messages(response.wsgi_request))
        self.assertTrue(any("Se eliminó 1 descanso" in message.message for message in messages))

    def test_unassigned_cell_lists_emergency_candidates(self) -> None:
        url = reverse("calendario:calendar-detail", args=[self.calendar.pk])
        target_date = self.calendar.start_date + timedelta(days=1)

        operator_rest = UserProfile.objects.create_user(
            cedula="3000",
            password="test",  # noqa: S106 - test credential
            nombres="Operario",
            apellidos="Descanso",
            telefono="3100000004",
        )
        operator_rest.employment_start_date = self.calendar.start_date - timedelta(days=15)
        operator_rest.save(update_fields=["employment_start_date"])

        OperatorRestPeriod.objects.create(
            operator=operator_rest,
            start_date=target_date,
            end_date=target_date,
            status=RestPeriodStatus.APPROVED,
            source=RestPeriodSource.MANUAL,
        )

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        rows = response.context["rows"]
        secondary_row = next(
            (row for row in rows if row["position"].pk == self.position_secondary.pk),
            None,
        )
        self.assertIsNotNone(secondary_row)

        cell = next((cell for cell in secondary_row["cells"] if cell["date"] == target_date), None)
        self.assertIsNotNone(cell)

        choices = {choice["id"]: choice for choice in cell["choices"]}
        self.assertIn(self.operator_manual.id, choices)
        self.assertIn("Disponible", choices[self.operator_manual.id]["label"])

        self.assertIn(operator_rest.id, choices)
        self.assertIn("En descanso", choices[operator_rest.id]["label"])

    def test_stats_count_gaps_as_critical_alerts(self) -> None:
        url = reverse("calendario:calendar-detail", args=[self.calendar.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        stats = response.context["stats"]
        self.assertGreater(stats["gaps"], 0)
        self.assertEqual(stats["critical"], stats["gaps"])

    def test_rest_rows_include_manual_rest_operator(self) -> None:
        rest_start = self.calendar.start_date + timedelta(days=1)
        rest_end = rest_start + timedelta(days=1)

        OperatorRestPeriod.objects.create(
            operator=self.operator_manual,
            start_date=rest_start,
            end_date=rest_end,
            status=RestPeriodStatus.APPROVED,
            source=RestPeriodSource.MANUAL,
        )

        url = reverse("calendario:calendar-detail", args=[self.calendar.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        rest_rows = response.context["rest_rows"]
        self.assertTrue(rest_rows)

        day_index = (rest_start - self.calendar.start_date).days
        manual_present = any(
            row["cells"][day_index]["operator_id"] == self.operator_manual.id
            and row["cells"][day_index]["state"] == REST_CELL_STATE_REST
            for row in rest_rows
        )
        self.assertTrue(manual_present)

        rest_summary = response.context["rest_summary"]
        self.assertIn(str(self.operator_manual.id), rest_summary)

    def test_rest_rows_include_unassigned_days_for_operator(self) -> None:
        url = reverse("calendario:calendar-detail", args=[self.calendar.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        rest_rows = response.context["rest_rows"]
        operator_name = self.operator_conflict.get_full_name()
        operator_row = next((row for row in rest_rows if row["slot"] == operator_name), None)
        self.assertIsNotNone(operator_row)

        self.assertTrue(
            any(cell["state"] == REST_CELL_STATE_UNASSIGNED for cell in operator_row["cells"])
        )

    def test_create_assignment_rejects_out_of_range_position(self) -> None:
        self.position_secondary.valid_from = self.calendar.start_date + timedelta(days=2)
        self.position_secondary.save(update_fields=["valid_from"])

        url = reverse("calendario:calendar-detail", args=[self.calendar.pk])
        target_date = self.calendar.start_date + timedelta(days=1)

        response = self.client.post(
            url,
            data={
                "action": "create-assignment",
                "position_id": self.position_secondary.pk,
                "date": target_date.isoformat(),
                "operator_id": self.operator_manual.pk,
            },
        )

        self.assertRedirects(response, url)
        exists = ShiftAssignment.objects.filter(
            calendar=self.calendar,
            position=self.position_secondary,
            date=target_date,
        ).exists()
        self.assertFalse(exists)

        messages = list(get_messages(response.wsgi_request))
        self.assertTrue(
            any("La posición no está vigente para la fecha seleccionada." in message.message for message in messages)
        )

    def test_regenerate_clears_related_records(self) -> None:
        OperatorRestPeriod.objects.create(
            operator=self.operator_initial,
            start_date=self.calendar.start_date,
            end_date=self.calendar.start_date + timedelta(days=1),
            status=RestPeriodStatus.APPROVED,
            source=RestPeriodSource.CALENDAR,
            calendar=self.calendar,
        )
        WorkloadSnapshot.objects.create(
            calendar=self.calendar,
            operator=self.operator_initial,
            total_shifts=2,
            day_shifts=2,
            night_shifts=0,
            rest_days=0,
            overtime_days=0,
            overtime_points_total=0,
            month_reference=date(self.calendar.start_date.year, self.calendar.start_date.month, 1),
        )

        url = reverse("calendario:calendar-detail", args=[self.calendar.pk])
        response = self.client.post(
            url,
            data={
                "action": "regenerate",
            },
        )

        self.assertRedirects(response, url)
        self.assertFalse(
            ShiftAssignment.objects.filter(
                pk__in=[self.assignment_primary.pk, self.assignment_conflict.pk]
            ).exists()
        )
        self.assertGreater(ShiftAssignment.objects.filter(calendar=self.calendar).count(), 0)
        self.assertFalse(OperatorRestPeriod.objects.filter(calendar=self.calendar).exists())
        self.assertFalse(self.calendar.workload_snapshots.exists())

        messages = list(get_messages(response.wsgi_request))
        self.assertTrue(
            any("Calendario regenerado" in message.message for message in messages)
        )


class CalendarDetailViewModifyCalendarTests(TestCase):
    def setUp(self) -> None:
        self.user = UserProfile.objects.create_user(
            cedula="3000",
            password="test",  # noqa: S106 - test credential
            nombres="Planificador",
            apellidos="Operaciones",
            telefono="3100000004",
        )
        self.client.force_login(self.user)

        self.farm = Farm.objects.create(name="Pinares")
        self.calendar = ShiftCalendar.objects.create(
            name="Semana aprobada",
            start_date=date(2025, 8, 4),
            end_date=date(2025, 8, 10),
            status=CalendarStatus.APPROVED,
            created_by=self.user,
            approved_by=self.user,
        )

    def test_mark_modified_changes_status(self) -> None:
        url = reverse("calendario:calendar-detail", args=[self.calendar.pk])
        response = self.client.post(
            url,
            data={
                "action": "mark-modified",
            },
        )

        self.assertRedirects(response, url)
        self.calendar.refresh_from_db()
        self.assertEqual(self.calendar.status, CalendarStatus.MODIFIED)

        messages = list(get_messages(response.wsgi_request))
        self.assertTrue(
            any("estado modificado" in message.message for message in messages)
        )

    def test_calendar_can_be_reapproved_and_modified_again(self) -> None:
        url = reverse("calendario:calendar-detail", args=[self.calendar.pk])

        # First modification
        self.client.post(url, data={"action": "mark-modified"})
        self.calendar.refresh_from_db()
        self.assertEqual(self.calendar.status, CalendarStatus.MODIFIED)

        # Re-approve
        response = self.client.post(url, data={"action": "approve"})
        self.assertRedirects(response, url)
        self.calendar.refresh_from_db()
        self.assertEqual(self.calendar.status, CalendarStatus.APPROVED)

        # Modify again
        response = self.client.post(url, data={"action": "mark-modified"})
        self.assertRedirects(response, url)
        self.calendar.refresh_from_db()
        self.assertEqual(self.calendar.status, CalendarStatus.MODIFIED)


class CalendarActivePositionVisibilityTests(TestCase):
    def setUp(self) -> None:
        self.user = UserProfile.objects.create_user(
            cedula="4000",
            password="test",  # noqa: S106 - test credential
            nombres="Analista",
            apellidos="Coberturas",
            telefono="3100000005",
        )
        self.client.force_login(self.user)

        self.farm = Farm.objects.create(name="Mirador")

        self.category, _created = PositionCategory.objects.get_or_create(
            code=PositionCategoryCode.GALPONERO_PRODUCCION_DIA,
            defaults={
                "shift_type": ShiftType.DAY,
                "rest_max_consecutive_days": 8,
                "rest_post_shift_days": 0,
                "rest_monthly_days": 5,
            },
        )

        self.category.rest_max_consecutive_days = 8
        self.category.rest_post_shift_days = 0
        self.category.rest_monthly_days = 5
        self.category.save()

    def test_summary_excludes_inactive_positions_without_assignments(self) -> None:
        calendar = ShiftCalendar.objects.create(
            name="Semana operativa",
            start_date=date(2025, 9, 1),
            end_date=date(2025, 9, 3),
            status=CalendarStatus.DRAFT,
            created_by=self.user,
        )

        active_position = PositionDefinition.objects.create(
            name="Posición activa",
            code="ACTIVE-1",
            category=self.category,
            farm=self.farm,
            valid_from=calendar.start_date,
            valid_until=calendar.end_date,
        )
        inactive_position = PositionDefinition.objects.create(
            name="Posición inactiva",
            code="INACTIVE-1",
            category=self.category,
            farm=self.farm,
            valid_from=calendar.end_date + timedelta(days=1),
            valid_until=calendar.end_date + timedelta(days=7),
        )

        url = reverse("calendario-api:calendar-summary", args=[calendar.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        payload = response.json()
        position_codes = [row["position"]["code"] for row in payload["rows"]]
        self.assertIn(active_position.code, position_codes)
        self.assertNotIn(inactive_position.code, position_codes)

    def test_detail_marks_slots_outside_validity(self) -> None:
        calendar = ShiftCalendar.objects.create(
            name="Semana parcial",
            start_date=date(2025, 10, 6),
            end_date=date(2025, 10, 8),
            status=CalendarStatus.DRAFT,
            created_by=self.user,
        )

        position = PositionDefinition.objects.create(
            name="Posición parcial",
            code="PARTIAL-CTX",
            category=self.category,
            farm=self.farm,
            valid_from=calendar.start_date + timedelta(days=1),
            valid_until=calendar.end_date,
        )

        response = self.client.get(reverse("calendario:calendar-detail", args=[calendar.pk]))
        self.assertEqual(response.status_code, 200)

        rows = response.context["rows"]
        target_row = next(row for row in rows if row["position"].id == position.id)
        slot_map = {cell["date"]: cell["is_position_active"] for cell in target_row["cells"]}

        self.assertFalse(slot_map[calendar.start_date])
        self.assertTrue(slot_map[calendar.start_date + timedelta(days=1)])

        inactive_cell = next(cell for cell in target_row["cells"] if cell["date"] == calendar.start_date)
        self.assertEqual(inactive_cell["choices"], [])

    def test_eligible_operators_empty_outside_position_range(self) -> None:
        calendar = ShiftCalendar.objects.create(
            name="Semana parcial",
            start_date=date(2025, 10, 6),
            end_date=date(2025, 10, 8),
            status=CalendarStatus.DRAFT,
            created_by=self.user,
        )

        position = PositionDefinition.objects.create(
            name="Posición parcial",
            code="PARTIAL-1",
            category=self.category,
            farm=self.farm,
            valid_from=calendar.start_date + timedelta(days=1),
            valid_until=calendar.end_date,
        )

        operator = UserProfile.objects.create_user(
            cedula="4001",
            password="test",  # noqa: S106 - test credential
            nombres="Operario",
            apellidos="Disponible",
            telefono="3100000006",
        )

        operator.suggested_positions.add(position)

        url = reverse(
            "calendario-api:calendar-eligible-operators",
            args=[calendar.pk],
        )

        response_outside = self.client.get(
            url,
            {
                "position": str(position.pk),
                "date": calendar.start_date.isoformat(),
            },
        )
        self.assertEqual(response_outside.status_code, 200)
        self.assertEqual(response_outside.json().get("results"), [])

        valid_date = calendar.start_date + timedelta(days=1)
        response_inside = self.client.get(
            url,
            {
                "position": str(position.pk),
                "date": valid_date.isoformat(),
            },
        )
        self.assertEqual(response_inside.status_code, 200)
        self.assertEqual(len(response_inside.json().get("results", [])), 1)

    def test_summary_marks_slots_outside_validity(self) -> None:
        calendar = ShiftCalendar.objects.create(
            name="Semana parcial resumen",
            start_date=date(2025, 11, 4),
            end_date=date(2025, 11, 6),
            status=CalendarStatus.DRAFT,
            created_by=self.user,
        )

        position = PositionDefinition.objects.create(
            name="Posición resumen",
            code="PARTIAL-SUMMARY",
            category=self.category,
            farm=self.farm,
            valid_from=calendar.start_date + timedelta(days=1),
            valid_until=calendar.end_date,
        )

        url = reverse("calendario-api:calendar-summary", args=[calendar.pk])
        payload = self.client.get(url).json()

        row = next(item for item in payload["rows"] if item["position"]["id"] == position.id)
        slot_map = {
            cell["date"]: cell["is_position_active"]
            for cell in row["cells"]
        }

        self.assertFalse(slot_map[calendar.start_date.isoformat()])
        self.assertTrue(slot_map[(calendar.start_date + timedelta(days=1)).isoformat()])

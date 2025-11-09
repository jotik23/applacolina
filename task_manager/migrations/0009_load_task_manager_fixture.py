from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from django.core.management import call_command
from django.db import migrations
from task_manager.services import suppress_task_assignment_sync


ARRAY_COLUMNS = (
    ("fortnight_days", "smallint[] NOT NULL DEFAULT '{}'::smallint[]"),
    ("monthly_week_days", "smallint[] NOT NULL DEFAULT '{}'::smallint[]"),
)

COLUMN_DEFINITIONS = (
    ("is_mandatory", "boolean NOT NULL DEFAULT FALSE"),
    ("criticality_level", "varchar(16) NOT NULL DEFAULT 'medium'"),
)

FIXTURE_FILENAME = "task_manager_data.json"


def load_task_manager_fixture(apps, schema_editor):
    connection = schema_editor.connection
    quoted_table = schema_editor.quote_name("task_manager_taskdefinition")

    with connection.cursor() as cursor:
        for column_name, column_definition in COLUMN_DEFINITIONS:
            cursor.execute(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = %s AND column_name = %s
                """,
                ["task_manager_taskdefinition", column_name],
            )
            if cursor.fetchone() is None:
                cursor.execute(
                    f"ALTER TABLE {quoted_table} "
                    f"ADD COLUMN {schema_editor.quote_name(column_name)} {column_definition}"
                )
                cursor.execute(
                    f"ALTER TABLE {quoted_table} "
                    f"ALTER COLUMN {schema_editor.quote_name(column_name)} DROP DEFAULT"
                )

        for column_name, column_definition in ARRAY_COLUMNS:
            cursor.execute(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = %s AND column_name = %s
                """,
                ["task_manager_taskdefinition", column_name],
            )
            if cursor.fetchone() is None:
                cursor.execute(
                    f"ALTER TABLE {quoted_table} "
                    f"ADD COLUMN {schema_editor.quote_name(column_name)} {column_definition}"
                )
                cursor.execute(
                    f"ALTER TABLE {quoted_table} "
                    f"ALTER COLUMN {schema_editor.quote_name(column_name)} DROP DEFAULT"
                )

    TaskStatus = apps.get_model("task_manager", "TaskStatus")
    if TaskStatus.objects.exists():
        # Data already present; skip loading to avoid duplicate PK errors.
        return

    fixture_path = Path(__file__).resolve().parent.parent / "fixtures" / FIXTURE_FILENAME
    if not fixture_path.exists():
        raise FileNotFoundError(f"Fixture file missing: {fixture_path}")

    with fixture_path.open("r", encoding="utf-8") as fixture_fp:
        fixture_payload = json.load(fixture_fp)

    collaborator_ids = sorted(
        {
            entry["fields"].get("collaborator")
            for entry in fixture_payload
            if entry.get("model") == "task_manager.taskdefinition"
            and entry["fields"].get("collaborator") is not None
        }
    )
    position_ids = sorted(
        {
            entry["fields"].get("position")
            for entry in fixture_payload
            if entry.get("model") == "task_manager.taskdefinition"
            and entry["fields"].get("position") is not None
        }
    )
    room_ids = sorted(
        {
            room_id
            for entry in fixture_payload
            if entry.get("model") == "task_manager.taskdefinition"
            for room_id in entry["fields"].get("rooms") or []
        }
    )

    UserProfile = apps.get_model("personal", "UserProfile")
    for collaborator_id in collaborator_ids:
        if UserProfile.objects.filter(pk=collaborator_id).exists():
            continue
        UserProfile.objects.create(
            id=collaborator_id,
            cedula=f"TMP-COLLAB-{collaborator_id}",
            password="!",
            nombres="Colaborador fixture",
            apellidos=f"#{collaborator_id}",
            telefono=f"000000{collaborator_id:03d}",
            is_active=True,
            is_staff=False,
            is_superuser=False,
        )

    Farm = apps.get_model("production", "Farm")
    ChickenHouse = apps.get_model("production", "ChickenHouse")
    Room = apps.get_model("production", "Room")
    PositionCategory = apps.get_model("personal", "PositionCategory")
    PositionDefinition = apps.get_model("personal", "PositionDefinition")

    farm, _ = Farm.objects.get_or_create(
        id=1,
        defaults={"name": "Granja Fixture"},
    )
    chicken_house, _ = ChickenHouse.objects.get_or_create(
        id=1,
        defaults={"farm": farm, "name": "Galpón Fixture", "area_m2": 1000},
    )

    for room_id in room_ids:
        Room.objects.get_or_create(
            id=room_id,
            defaults={
                "chicken_house": chicken_house,
                "name": f"Salón {room_id}",
                "area_m2": 100,
            },
        )

    category, _ = PositionCategory.objects.get_or_create(
        code="OFICIOS_VARIOS",
        defaults={
            "shift_type": "day",
            "rest_max_consecutive_days": 8,
            "rest_post_shift_days": 0,
            "rest_monthly_days": 5,
            "is_active": True,
        },
    )

    room_queryset = Room.objects.filter(pk__in=room_ids)
    for position_id in position_ids:
        if PositionDefinition.objects.filter(pk=position_id).exists():
            continue
        position = PositionDefinition.objects.create(
            id=position_id,
            name=f"Posición Fixture {position_id}",
            code=f"POS-{position_id}",
            category=category,
            valid_from=date(2020, 1, 1),
            display_order=position_id,
            farm=farm,
            chicken_house=chicken_house,
        )
        if room_queryset.exists():
            position.rooms.set(room_queryset)

    with suppress_task_assignment_sync():
        call_command("loaddata", str(fixture_path))


class Migration(migrations.Migration):
    dependencies = [
        ("task_manager", "0008_alter_taskdefinition_options_and_more"),
    ]

    operations = [
        migrations.RunPython(load_task_manager_fixture, migrations.RunPython.noop),
    ]

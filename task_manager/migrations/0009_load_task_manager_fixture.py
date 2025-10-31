from __future__ import annotations

from pathlib import Path

from django.core.management import call_command
from django.db import migrations


ARRAY_COLUMNS = (
    ("fortnight_days", "smallint[] NOT NULL DEFAULT '{}'::smallint[]"),
    ("monthly_week_days", "smallint[] NOT NULL DEFAULT '{}'::smallint[]"),
)


FIXTURE_FILENAME = "task_manager_data.json"


def load_task_manager_fixture(apps, schema_editor):
    connection = schema_editor.connection
    quoted_table = schema_editor.quote_name("task_manager_taskdefinition")

    with connection.cursor() as cursor:
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

    fixture_path = (
        Path(__file__).resolve().parent.parent / "fixtures" / FIXTURE_FILENAME
    )
    if not fixture_path.exists():
        raise FileNotFoundError(f"Fixture file missing: {fixture_path}")

    call_command("loaddata", str(fixture_path))


class Migration(migrations.Migration):
    dependencies = [
        ("task_manager", "0008_alter_taskdefinition_options_and_more"),
    ]

    operations = [
        migrations.RunPython(load_task_manager_fixture, migrations.RunPython.noop),
    ]

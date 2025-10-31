from __future__ import annotations

from pathlib import Path

from django.core.management import call_command
from django.db import migrations


FIXTURE_FILENAME = "task_manager_data.json"


def load_task_manager_fixture(apps, schema_editor):
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

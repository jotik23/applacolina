from __future__ import annotations

from pathlib import Path

from django.core.management import call_command
from django.db import migrations


def load_task_manager_fixture(apps, schema_editor):
    TaskStatus = apps.get_model("task_manager", "TaskStatus")
    if TaskStatus.objects.exists():
        # Data already present; skip loading to avoid duplicate PK errors.
        return

    app_config = apps.get_app_config("task_manager")
    fixture_path = Path(app_config.path) / "fixtures" / "task_manager_data.json"
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

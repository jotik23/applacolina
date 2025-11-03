from __future__ import annotations

from django.db import migrations


def migrate_scope_to_rooms(apps, schema_editor) -> None:
    TaskDefinition = apps.get_model("task_manager", "TaskDefinition")
    Room = apps.get_model("production", "Room")

    room_model = Room

    for task in TaskDefinition.objects.all().iterator():
        room_ids = set(task.rooms.values_list("id", flat=True))

        farm_ids = list(task.farms.values_list("id", flat=True))
        if farm_ids:
            farm_room_ids = room_model.objects.filter(
                chicken_house__farm_id__in=farm_ids
            ).values_list("id", flat=True)
            room_ids.update(farm_room_ids)

        house_ids = list(task.chicken_houses.values_list("id", flat=True))
        if house_ids:
            house_room_ids = room_model.objects.filter(
                chicken_house_id__in=house_ids
            ).values_list("id", flat=True)
            room_ids.update(house_room_ids)

        task.rooms.set(room_ids)


class Migration(migrations.Migration):
    dependencies = [
        ("task_manager", "0020_alter_taskdefinition_record_format"),
    ]

    operations = [
        migrations.RunPython(migrate_scope_to_rooms, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="taskdefinition",
            name="farms",
        ),
        migrations.RemoveField(
            model_name="taskdefinition",
            name="chicken_houses",
        ),
    ]

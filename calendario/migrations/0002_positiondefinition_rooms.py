from django.db import migrations, models


def copy_room_to_rooms(apps, schema_editor):
    PositionDefinition = apps.get_model("calendario", "PositionDefinition")
    db_alias = schema_editor.connection.alias
    for position in PositionDefinition.objects.using(db_alias).all():
        room_id = getattr(position, "room_id", None)
        if room_id:
            position.rooms.add(room_id)


def restore_room_from_rooms(apps, schema_editor):
    PositionDefinition = apps.get_model("calendario", "PositionDefinition")
    db_alias = schema_editor.connection.alias
    for position in PositionDefinition.objects.using(db_alias).all():
        rooms = list(position.rooms.using(db_alias).all())
        room_id = rooms[0].id if rooms else None
        setattr(position, "room_id", room_id)
        position.save(update_fields=["room"])


class Migration(migrations.Migration):
    dependencies = [
        ("granjas", "0001_initial"),
        ("calendario", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="positiondefinition",
            name="rooms",
            field=models.ManyToManyField(
                blank=True,
                related_name="position_definitions",
                to="granjas.room",
                verbose_name="Salones",
            ),
        ),
        migrations.RunPython(copy_room_to_rooms, restore_room_from_rooms),
        migrations.RemoveField(
            model_name="positiondefinition",
            name="room",
        ),
    ]

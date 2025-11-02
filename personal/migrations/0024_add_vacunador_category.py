from django.db import migrations


def add_vacunador_category(apps, schema_editor):
    PositionCategory = apps.get_model("personal", "PositionCategory")
    PositionCategory.objects.update_or_create(
        code="VACUNADOR",
        defaults={
            "shift_type": "day",
            "rest_max_consecutive_days": 8,
            "rest_post_shift_days": 0,
            "rest_monthly_days": 5,
            "is_active": True,
        },
    )


class Migration(migrations.Migration):
    dependencies = [
        ("personal", "0023_positiondefinition_handoff_position"),
    ]

    operations = [
        migrations.RunPython(add_vacunador_category, migrations.RunPython.noop),
    ]


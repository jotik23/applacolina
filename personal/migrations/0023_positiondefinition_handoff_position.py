from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("personal", "0022_alter_positioncategory_code"),
    ]

    operations = [
        migrations.AddField(
            model_name="positiondefinition",
            name="handoff_position",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.SET_NULL,
                related_name="handoff_sources",
                to="personal.positiondefinition",
                verbose_name="Entrega turno a",
            ),
        ),
    ]

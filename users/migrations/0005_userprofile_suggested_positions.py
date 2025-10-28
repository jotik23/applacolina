from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("calendario", "0012_positioncategory_automatic_rest_days"),
        ("users", "0004_userprofile_employment_end_date"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="suggested_positions",
            field=models.ManyToManyField(
                blank=True,
                help_text="Posiciones recomendadas para priorizar asignaciones automáticas.",
                related_name="preferred_operators",
                to="calendario.positiondefinition",
                verbose_name="Posiciones sugeridas",
            ),
        ),
    ]

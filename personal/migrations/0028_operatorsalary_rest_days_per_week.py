from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("personal", "0027_positiondefinition_job_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="operatorsalary",
            name="rest_days_per_week",
            field=models.PositiveSmallIntegerField(
                default=1,
                verbose_name="Descansos semanales",
                help_text="Número de días de descanso remunerado por semana para este esquema.",
            ),
        ),
    ]

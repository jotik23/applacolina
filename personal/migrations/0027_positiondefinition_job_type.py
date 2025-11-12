from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("personal", "0026_operatorsalary_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="positiondefinition",
            name="job_type",
            field=models.CharField(
                choices=[
                    ("production", "Producción"),
                    ("classification", "Clasificación"),
                    ("administrative", "Administrativo"),
                    ("sales", "Ventas"),
                ],
                default="production",
                max_length=32,
                verbose_name="Tipo de puesto",
            ),
        ),
    ]

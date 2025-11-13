from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("administration", "0031_purchaserequest_assigned_manager"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PayrollSnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("start_date", models.DateField(verbose_name="Fecha inicial")),
                ("end_date", models.DateField(verbose_name="Fecha final")),
                (
                    "payload",
                    models.JSONField(blank=True, default=dict, verbose_name="Resumen almacenado"),
                ),
                ("last_computed_at", models.DateTimeField(blank=True, null=True, verbose_name="Calculado en")),
                (
                    "last_action",
                    models.CharField(
                        choices=[
                            ("generate", "Generación inicial"),
                            ("update", "Actualización"),
                            ("apply", "Ajuste manual"),
                            ("export", "Exportación"),
                        ],
                        default="generate",
                        max_length=20,
                        verbose_name="Última acción",
                    ),
                ),
                (
                    "last_computed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="payroll_snapshots",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Nómina almacenada",
                "verbose_name_plural": "Nóminas almacenadas",
                "ordering": ("-start_date", "-end_date"),
            },
        ),
        migrations.AddConstraint(
            model_name="payrollsnapshot",
            constraint=models.UniqueConstraint(
                fields=("start_date", "end_date"), name="unique_payroll_period_snapshot"
            ),
        ),
    ]

from __future__ import annotations

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    replaces = [('calendario', '0009_operatorrestperiod')]


    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("personal", "0008_category_rules_update"),
    ]

    operations = [
        migrations.CreateModel(
            name="OperatorRestPeriod",
            fields=[
                (
                    "id",
                    models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID"),
                ),
                ("start_date", models.DateField(verbose_name="Inicio")),
                ("end_date", models.DateField(verbose_name="Fin")),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("planned", "Planificado"),
                            ("approved", "Aprobado"),
                            ("confirmed", "Confirmado"),
                            ("expired", "Expirado"),
                            ("cancelled", "Cancelado"),
                        ],
                        default="planned",
                        max_length=16,
                        verbose_name="Estado",
                    ),
                ),
                (
                    "source",
                    models.CharField(
                        choices=[("manual", "Manual"), ("calendar", "Calendario")],
                        default="manual",
                        max_length=16,
                        verbose_name="Origen",
                    ),
                ),
                ("notes", models.TextField(blank=True, verbose_name="Notas")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "calendar",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=models.SET_NULL,
                        related_name="rest_periods",
                        to="personal.shiftcalendar",
                        verbose_name="Calendario origen",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=models.SET_NULL,
                        related_name="created_rest_periods",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Creado por",
                    ),
                ),
                (
                    "operator",
                    models.ForeignKey(
                        on_delete=models.CASCADE,
                        related_name="rest_periods",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Operario",
                    ),
                ),
            ],
            options={
                "verbose_name": "Periodo de descanso",
                "verbose_name_plural": "Periodos de descanso",
                "ordering": ("-start_date", "-created_at"),
                "db_table": "calendario_operatorrestperiod",
            },
        ),
        migrations.AddIndex(
            model_name="operatorrestperiod",
            index=models.Index(fields=["operator", "start_date"], name="calendario_o_operato_0eec88_idx"),
        ),
        migrations.AddIndex(
            model_name="operatorrestperiod",
            index=models.Index(fields=["operator", "end_date"], name="calendario_o_operato_a71ea4_idx"),
        ),
    ]

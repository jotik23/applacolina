from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("production", "0006_alter_productionrecord_average_egg_weight"),
    ]

    operations = [
        migrations.CreateModel(
            name="WeightSampleSession",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("date", models.DateField(verbose_name="Fecha")),
                ("unit", models.CharField(default="g", max_length=16, verbose_name="Unidad")),
                (
                    "tolerance_percent",
                    models.PositiveSmallIntegerField(default=10, verbose_name="Tolerancia uniformidad (%)"),
                ),
                (
                    "minimum_sample",
                    models.PositiveSmallIntegerField(default=30, verbose_name="Muestra mínima sugerida"),
                ),
                ("birds", models.PositiveIntegerField(blank=True, null=True, verbose_name="Aves en salón")),
                ("sample_size", models.PositiveIntegerField(default=0, verbose_name="Tamaño de muestra")),
                (
                    "average_grams",
                    models.DecimalField(
                        blank=True,
                        decimal_places=2,
                        max_digits=10,
                        null=True,
                        verbose_name="Peso promedio (g)",
                    ),
                ),
                (
                    "variance_grams",
                    models.DecimalField(
                        blank=True,
                        decimal_places=2,
                        max_digits=12,
                        null=True,
                        verbose_name="Varianza (g²)",
                    ),
                ),
                (
                    "min_grams",
                    models.DecimalField(
                        blank=True,
                        decimal_places=2,
                        max_digits=10,
                        null=True,
                        verbose_name="Peso mínimo (g)",
                    ),
                ),
                (
                    "max_grams",
                    models.DecimalField(
                        blank=True,
                        decimal_places=2,
                        max_digits=10,
                        null=True,
                        verbose_name="Peso máximo (g)",
                    ),
                ),
                (
                    "uniformity_percent",
                    models.DecimalField(
                        blank=True,
                        decimal_places=2,
                        max_digits=5,
                        null=True,
                        verbose_name="Uniformidad (%)",
                    ),
                ),
                (
                    "within_tolerance",
                    models.PositiveIntegerField(
                        default=0,
                        verbose_name="Muestras dentro de la tolerancia",
                    ),
                ),
                ("submitted_at", models.DateTimeField(blank=True, null=True, verbose_name="Enviado en")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="weight_sample_sessions_created",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Registrado por",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="weight_sample_sessions_updated",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Actualizado por",
                    ),
                ),
                (
                    "production_record",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="weight_sample_sessions",
                        to="production.productionrecord",
                        verbose_name="Registro de producción",
                    ),
                ),
                (
                    "room",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="weight_sample_sessions",
                        to="production.room",
                        verbose_name="Salón",
                    ),
                ),
            ],
            options={
                "verbose_name": "Sesión de pesaje",
                "verbose_name_plural": "Sesiones de pesaje",
                "ordering": ("-date", "-updated_at"),
                "unique_together": {("date", "room")},
            },
        ),
        migrations.CreateModel(
            name="WeightSample",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "grams",
                    models.DecimalField(decimal_places=2, max_digits=8, verbose_name="Peso (g)"),
                ),
                (
                    "recorded_at",
                    models.DateTimeField(default=django.utils.timezone.now, verbose_name="Registrado en"),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "recorded_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="weight_samples_recorded",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Registrado por",
                    ),
                ),
                (
                    "session",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="samples",
                        to="production.weightsamplesession",
                        verbose_name="Sesión",
                    ),
                ),
            ],
            options={
                "verbose_name": "Peso registrado",
                "verbose_name_plural": "Pesos registrados",
                "ordering": ("created_at",),
            },
        ),
    ]

from __future__ import annotations

from django.core.validators import MinValueValidator
from django.db import migrations, models
import django.db.models.deletion


CATEGORY_CHOICES = [
    ("GALPONERO_PRODUCCION_DIA", "Galponero producción día"),
    ("GALPONERO_LEVANTE_DIA", "Galponero levante día"),
    ("GALPONERO_PRODUCCION_NOCHE", "Galponero producción noche"),
    ("GALPONERO_LEVANTE_NOCHE", "Galponero levante noche"),
    ("CLASIFICADOR_DIA", "Clasificador día"),
    ("CLASIFICADOR_NOCHE", "Clasificador noche"),
    ("LIDER_GRANJA", "Líder de granja"),
    ("SUPERVISOR", "Supervisor"),
    ("LIDER_TECNICO", "Líder técnico"),
    ("OFICIOS_VARIOS", "Oficios varios"),
]

SHIFT_CHOICES = [
    ("day", "Día"),
    ("night", "Noche"),
    ("mixed", "Mixto"),
]

ALERT_LEVEL_CHOICES = [
    ("none", "Sin alerta"),
    ("warn", "Desajuste moderado"),
    ("critical", "Desajuste crítico"),
]


def seed_position_categories(apps, schema_editor) -> None:
    PositionCategory = apps.get_model("calendario", "PositionCategory")

    defaults_map = {
        "GALPONERO_PRODUCCION_DIA": {
            "shift_type": "day",
            "default_extra_day_limit": 3,
        },
        "GALPONERO_LEVANTE_DIA": {
            "shift_type": "day",
            "default_extra_day_limit": 3,
        },
        "GALPONERO_PRODUCCION_NOCHE": {
            "shift_type": "night",
            "default_extra_day_limit": 2,
        },
        "GALPONERO_LEVANTE_NOCHE": {
            "shift_type": "night",
            "default_extra_day_limit": 2,
        },
        "CLASIFICADOR_DIA": {
            "shift_type": "day",
            "default_extra_day_limit": 3,
        },
        "CLASIFICADOR_NOCHE": {
            "shift_type": "night",
            "default_extra_day_limit": 2,
        },
        "LIDER_GRANJA": {
            "shift_type": "mixed",
            "default_extra_day_limit": 3,
        },
        "SUPERVISOR": {
            "shift_type": "day",
            "default_extra_day_limit": 3,
        },
        "LIDER_TECNICO": {
            "shift_type": "day",
            "default_extra_day_limit": 3,
        },
        "OFICIOS_VARIOS": {
            "shift_type": "day",
            "default_extra_day_limit": 3,
        },
    }

    for code, label in CATEGORY_CHOICES:
        defaults = defaults_map.get(code, {"shift_type": "day", "default_extra_day_limit": 3})
        PositionCategory.objects.update_or_create(
            code=code,
            defaults={
                "name": label,
                "shift_type": defaults["shift_type"],
                "default_extra_day_limit": defaults["default_extra_day_limit"],
                "default_overtime_points": 1,
                "is_active": True,
            },
        )


def migrate_position_categories(apps, schema_editor) -> None:
    PositionCategory = apps.get_model("calendario", "PositionCategory")
    PositionDefinition = apps.get_model("calendario", "PositionDefinition")
    OperatorCapability = apps.get_model("calendario", "OperatorCapability")

    category_map = {category.code: category for category in PositionCategory.objects.all()}

    def resolve(code: str | None) -> PositionCategory | None:
        if not code:
            return None
        category = category_map.get(code)
        if category:
            return category

        # Create fallback category when encountering an unknown code.
        category = PositionCategory.objects.create(
            code=code,
            name=code.replace("_", " ").title(),
            shift_type="day",
            default_extra_day_limit=3,
            default_overtime_points=1,
            is_active=True,
        )
        category_map[code] = category
        return category

    for position in PositionDefinition.objects.all():
        code = getattr(position, "legacy_category", None)
        category = resolve(code)
        if category:
            position.category = category
            position.save(update_fields=["category"])

    for capability in OperatorCapability.objects.all():
        code = getattr(capability, "legacy_category", None)
        category = resolve(code)
        if category:
            capability.category = category
            capability.save(update_fields=["category"])


def reset_overload_rules(apps, schema_editor) -> None:
    OverloadAllowance = apps.get_model("calendario", "OverloadAllowance")
    OverloadAllowance.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("calendario", "0006_alter_positiondefinition_options_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="PositionCategory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "code",
                    models.CharField(
                        choices=CATEGORY_CHOICES,
                        max_length=64,
                        unique=True,
                        verbose_name="Código",
                    ),
                ),
                ("name", models.CharField(max_length=150, verbose_name="Nombre")),
                (
                    "shift_type",
                    models.CharField(
                        choices=SHIFT_CHOICES,
                        default="day",
                        max_length=16,
                        verbose_name="Turno",
                    ),
                ),
                (
                    "default_extra_day_limit",
                    models.PositiveSmallIntegerField(
                        default=3,
                        validators=[MinValueValidator(1)],
                        verbose_name="Máximo extra por defecto",
                    ),
                ),
                (
                    "default_overtime_points",
                    models.PositiveSmallIntegerField(
                        default=1,
                        validators=[MinValueValidator(1)],
                        verbose_name="Puntos por día extra",
                    ),
                ),
                ("is_active", models.BooleanField(default=True, verbose_name="Activo")),
            ],
            options={
                "verbose_name": "Categoría de posición",
                "verbose_name_plural": "Categorías de posiciones",
                "ordering": ("name", "code"),
            },
        ),
        migrations.RenameField(
            model_name="positiondefinition",
            old_name="category",
            new_name="legacy_category",
        ),
        migrations.RenameField(
            model_name="operatorcapability",
            old_name="category",
            new_name="legacy_category",
        ),
        migrations.AlterUniqueTogether(
            name="operatorcapability",
            unique_together=set(),
        ),
        migrations.AddField(
            model_name="positiondefinition",
            name="category",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="positions",
                to="calendario.positioncategory",
                verbose_name="Categoría",
            ),
        ),
        migrations.AddField(
            model_name="operatorcapability",
            name="category",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="capabilities",
                to="calendario.positioncategory",
                verbose_name="Categoría",
            ),
        ),
        migrations.RenameField(
            model_name="overloadallowance",
            old_name="highlight_level",
            new_name="alert_level",
        ),
        migrations.RenameField(
            model_name="overloadallowance",
            old_name="max_consecutive_extra_days",
            new_name="extra_day_limit",
        ),
        migrations.AlterUniqueTogether(
            name="overloadallowance",
            unique_together=set(),
        ),
        migrations.RemoveField(
            model_name="overloadallowance",
            name="active_from",
        ),
        migrations.RemoveField(
            model_name="overloadallowance",
            name="active_until",
        ),
        migrations.RemoveField(
            model_name="overloadallowance",
            name="role",
        ),
        migrations.AddField(
            model_name="overloadallowance",
            name="category",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="overload_rules",
                to="calendario.positioncategory",
                verbose_name="Categoría",
            ),
        ),
        migrations.AddField(
            model_name="overloadallowance",
            name="overtime_points",
            field=models.PositiveSmallIntegerField(
                default=1,
                validators=[MinValueValidator(1)],
                verbose_name="Puntos por día extra",
            ),
        ),
        migrations.AlterField(
            model_name="overloadallowance",
            name="alert_level",
            field=models.CharField(
                choices=ALERT_LEVEL_CHOICES,
                default="warn",
                max_length=16,
                verbose_name="Nivel de alerta",
            ),
        ),
        migrations.AlterField(
            model_name="overloadallowance",
            name="extra_day_limit",
            field=models.PositiveSmallIntegerField(
                default=3,
                validators=[MinValueValidator(1)],
                verbose_name="Máximo días extra consecutivos",
            ),
        ),
        migrations.AlterModelOptions(
            name="operatorcapability",
            options={
                "ordering": ("operator__apellidos", "operator__nombres", "category__name"),
                "verbose_name": "Capacidad de operario",
                "verbose_name_plural": "Capacidades de operarios",
            },
        ),
        migrations.AlterModelOptions(
            name="overloadallowance",
            options={
                "ordering": ("category__name",),
                "verbose_name": "Regla de sobrecarga",
                "verbose_name_plural": "Reglas de sobrecarga",
            },
        ),
        migrations.AddField(
            model_name="shiftassignment",
            name="overtime_points",
            field=models.PositiveSmallIntegerField(default=0, verbose_name="Puntos por sobrecarga"),
        ),
        migrations.AddField(
            model_name="workloadsnapshot",
            name="overtime_points_total",
            field=models.PositiveIntegerField(default=0, verbose_name="Puntos por sobrecarga"),
        ),
        migrations.RunPython(seed_position_categories, migrations.RunPython.noop),
        migrations.RunPython(migrate_position_categories, migrations.RunPython.noop),
        migrations.RunPython(reset_overload_rules, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="positiondefinition",
            name="category",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="positions",
                to="calendario.positioncategory",
                verbose_name="Categoría",
            ),
        ),
        migrations.AlterField(
            model_name="operatorcapability",
            name="category",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="capabilities",
                to="calendario.positioncategory",
                verbose_name="Categoría",
            ),
        ),
        migrations.AlterField(
            model_name="overloadallowance",
            name="category",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="overload_rules",
                to="calendario.positioncategory",
                verbose_name="Categoría",
            ),
        ),
        migrations.AlterUniqueTogether(
            name="operatorcapability",
            unique_together={("operator", "category")},
        ),
        migrations.AlterUniqueTogether(
            name="overloadallowance",
            unique_together={("category",)},
        ),
        migrations.RemoveField(
            model_name="positiondefinition",
            name="legacy_category",
        ),
        migrations.RemoveField(
            model_name="operatorcapability",
            name="legacy_category",
        ),
    ]

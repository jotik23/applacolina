from django.core.validators import MinValueValidator
from django.db import migrations, models


def copy_overload_allowances(apps, schema_editor):
    PositionCategory = apps.get_model("personal", "PositionCategory")
    OverloadAllowance = apps.get_model("personal", "OverloadAllowance")

    for allowance in OverloadAllowance.objects.select_related("category"):
        category = allowance.category
        if not category:
            continue
        category.extra_day_limit = allowance.extra_day_limit
        category.overtime_points = allowance.overtime_points
        category.overload_alert_level = allowance.alert_level
        category.save(
            update_fields=["extra_day_limit", "overtime_points", "overload_alert_level"]
        )


class Migration(migrations.Migration):

    replaces = [('calendario', '0008_category_rules_update')]

    dependencies = [
        ("personal", "0007_position_categories_refactor"),
    ]

    operations = [
        migrations.RenameField(
            model_name="positioncategory",
            old_name="default_extra_day_limit",
            new_name="extra_day_limit",
        ),
        migrations.RenameField(
            model_name="positioncategory",
            old_name="default_overtime_points",
            new_name="overtime_points",
        ),
        migrations.AddField(
            model_name="positioncategory",
            name="overload_alert_level",
            field=models.CharField(
                choices=[
                    ("none", "Sin alerta"),
                    ("warn", "Desajuste moderado"),
                    ("critical", "Desajuste crítico"),
                ],
                default="warn",
                max_length=16,
                verbose_name="Nivel de alerta de sobrecarga",
            ),
        ),
        migrations.AddField(
            model_name="positioncategory",
            name="rest_min_frequency",
            field=models.PositiveSmallIntegerField(
                default=6,
                validators=[MinValueValidator(1)],
                verbose_name="Frecuencia mínima de descanso",
            ),
        ),
        migrations.AddField(
            model_name="positioncategory",
            name="rest_min_consecutive_days",
            field=models.PositiveSmallIntegerField(
                default=5,
                validators=[MinValueValidator(1)],
                verbose_name="Días de descanso consecutivos mínimos",
            ),
        ),
        migrations.AddField(
            model_name="positioncategory",
            name="rest_max_consecutive_days",
            field=models.PositiveSmallIntegerField(
                default=8,
                validators=[MinValueValidator(1)],
                verbose_name="Días de descanso consecutivos máximos",
            ),
        ),
        migrations.AddField(
            model_name="positioncategory",
            name="rest_post_shift_days",
            field=models.PositiveSmallIntegerField(
                default=0, verbose_name="Descanso posterior al turno"
            ),
        ),
        migrations.AddField(
            model_name="positioncategory",
            name="rest_monthly_days",
            field=models.PositiveSmallIntegerField(
                default=5,
                validators=[MinValueValidator(1)],
                verbose_name="Descanso mensual requerido",
            ),
        ),
        migrations.AlterField(
            model_name="positioncategory",
            name="extra_day_limit",
            field=models.PositiveSmallIntegerField(
                default=3,
                validators=[MinValueValidator(1)],
                verbose_name="Máximo días extra consecutivos",
            ),
        ),
        migrations.RunPython(copy_overload_allowances, migrations.RunPython.noop),
        migrations.DeleteModel(
            name="RestPreference",
        ),
        migrations.DeleteModel(
            name="RestRule",
        ),
        migrations.DeleteModel(
            name="OverloadAllowance",
        ),
    ]

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("task_manager", "0030_merge_20251119_1229"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql="""
                        ALTER TABLE task_manager_taskdefinition
                        ADD COLUMN IF NOT EXISTS is_accumulative BOOLEAN NOT NULL DEFAULT FALSE;
                        ALTER TABLE task_manager_taskdefinition
                        ALTER COLUMN is_accumulative DROP DEFAULT;
                    """,
                    reverse_sql="""
                        ALTER TABLE task_manager_taskdefinition
                        DROP COLUMN IF EXISTS is_accumulative;
                    """,
                )
            ],
            state_operations=[
                migrations.AddField(
                    model_name="taskdefinition",
                    name="is_accumulative",
                    field=models.BooleanField(
                        default=False,
                        help_text=(
                            "Cuando está activo, las tareas pendientes permanecen visibles en la mini app hasta ser completadas."
                        ),
                        verbose_name="Acumulable en mini app",
                    ),
                ),
            ],
        ),
    ]

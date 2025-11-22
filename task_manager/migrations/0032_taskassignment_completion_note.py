from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("task_manager", "0031_taskdefinition_is_accumulative"),
    ]

    operations = [
        migrations.AddField(
            model_name="taskassignment",
            name="completion_note",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Comentario breve registrado al cerrar la tarea desde la mini app.",
                max_length=280,
                verbose_name="Nota de finalización",
            ),
        ),
    ]

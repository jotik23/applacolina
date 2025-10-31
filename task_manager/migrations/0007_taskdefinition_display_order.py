from django.db import migrations, models


def set_initial_display_order(apps, schema_editor):
    TaskDefinition = apps.get_model("task_manager", "TaskDefinition")
    tasks = list(TaskDefinition.objects.order_by("display_order", "name", "pk", "id"))
    if not tasks:
        return
    updated = []
    for index, task in enumerate(tasks, start=1):
        if task.display_order != index:
            task.display_order = index
            updated.append(task)
    if updated:
        TaskDefinition.objects.bulk_update(updated, ["display_order"])


def reset_display_order(apps, schema_editor):
    TaskDefinition = apps.get_model("task_manager", "TaskDefinition")
    TaskDefinition.objects.update(display_order=0)


class Migration(migrations.Migration):

    dependencies = [
        ("task_manager", "0006_alter_taskdefinition_task_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="taskdefinition",
            name="display_order",
            field=models.PositiveIntegerField(
                db_index=True, default=0, editable=False, verbose_name="Orden de visualización"
            ),
        ),
        migrations.RunPython(set_initial_display_order, reset_display_order),
    ]

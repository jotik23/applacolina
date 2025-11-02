from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("task_manager", "0016_alter_taskdefinition_options"),
    ]

    operations = [
        migrations.AlterField(
            model_name="taskassignment",
            name="collaborator",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.PROTECT,
                related_name="task_assignments",
                to="personal.userprofile",
                verbose_name="Colaborador",
            ),
        ),
        migrations.AlterUniqueTogether(
            name="taskassignment",
            unique_together=set(),
        ),
        migrations.AddConstraint(
            model_name="taskassignment",
            constraint=models.UniqueConstraint(
                condition=models.Q(collaborator__isnull=False),
                fields=("task_definition", "due_date", "collaborator"),
                name="task_assignment_unique_with_collaborator",
            ),
        ),
        migrations.AddConstraint(
            model_name="taskassignment",
            constraint=models.UniqueConstraint(
                condition=models.Q(collaborator__isnull=True),
                fields=("task_definition", "due_date"),
                name="task_assignment_unique_orphan_per_day",
            ),
        ),
    ]

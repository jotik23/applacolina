from __future__ import annotations

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("task_manager", "0021_taskdefinition_scope_to_rooms"),
    ]

    operations = [
        migrations.AddField(
            model_name="taskassignment",
            name="previous_collaborator",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.SET_NULL,
                related_name="previous_task_assignments",
                to=settings.AUTH_USER_MODEL,
                verbose_name="Colaborador previo",
            ),
        ),
    ]

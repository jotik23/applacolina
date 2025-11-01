from __future__ import annotations

from django.db import migrations


def grant_mini_app_permission(apps, schema_editor):
    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")
    UserProfile = apps.get_model("personal", "UserProfile")

    try:
        permission = Permission.objects.get(
            content_type__app_label="task_manager",
            codename="access_mini_app",
        )
    except Permission.DoesNotExist:  # pragma: no cover - safety guard during deploys
        return

    operario_group, _ = Group.objects.get_or_create(name="Operario")
    operario_group.permissions.add(permission)

    users = UserProfile.objects.all()
    for user in users.iterator():
        user.user_permissions.add(permission)


class Migration(migrations.Migration):

    dependencies = [
        ("task_manager", "0010_alter_taskdefinition_options"),
    ]

    operations = [
        migrations.RunPython(grant_mini_app_permission, migrations.RunPython.noop),
    ]

from django.apps import AppConfig


def _grant_mini_app_permission(sender, instance, created, **kwargs):
    if not created:
        return

    from django.contrib.auth.models import Permission

    try:
        permission = Permission.objects.get(
            content_type__app_label="task_manager",
            codename="access_mini_app",
        )
    except Permission.DoesNotExist:
        return

    instance.user_permissions.add(permission)


class TaskManagerConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "task_manager"
    verbose_name = "Gestor de tareas"

    def ready(self):
        from django.apps import apps as django_apps
        from django.db.models.signals import post_save

        UserProfile = django_apps.get_model("personal", "UserProfile")
        post_save.connect(
            _grant_mini_app_permission,
            sender=UserProfile,
            dispatch_uid="task_manager_grant_mini_app_permission",
        )

        # Ensure signal handlers are registered.
        from . import signals  # noqa: F401

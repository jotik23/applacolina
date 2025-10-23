from django.apps import AppConfig


class UsersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "users"
    verbose_name = "Usuarios"

    def ready(self) -> None:
        # Import signals or other initialization logic here if needed.
        return super().ready()


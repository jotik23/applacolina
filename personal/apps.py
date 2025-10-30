from django.apps import AppConfig


class PersonalConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "personal"
    verbose_name = "Personal"

    def ready(self) -> None:
        # Import side effects here when signals are defined.
        try:
            import personal.signals  # noqa: F401
        except ModuleNotFoundError:
            # Signals are optional at this stage.
            pass

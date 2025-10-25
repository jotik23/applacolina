from django.apps import AppConfig


class CalendarioConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "calendario"
    verbose_name = "Calendario operativo"

    def ready(self) -> None:
        # Import side effects here when signals are defined.
        try:
            import calendario.signals  # noqa: F401
        except ModuleNotFoundError:
            # Signals are optional at this stage.
            pass

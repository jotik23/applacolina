from django.apps import AppConfig


class InventoryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "inventory"
    verbose_name = "Inventario de productos"

    def ready(self) -> None:  # pragma: no cover - import side effects
        from . import signals  # noqa: F401

from django.db import models

from granjas.models import BirdBatch


class ProductionRecord(models.Model):
    bird_batch = models.ForeignKey(
        BirdBatch,
        on_delete=models.CASCADE,
        related_name="production_records",
        verbose_name="Lote de aves",
    )
    date = models.DateField(verbose_name="Fecha")
    production = models.DecimalField(
        verbose_name="Produccion",
        max_digits=10,
        decimal_places=2,
    )
    consumption = models.DecimalField(
        verbose_name="Consumo",
        max_digits=10,
        decimal_places=2,
    )
    mortality = models.PositiveIntegerField(verbose_name="Mortalidad")
    discard = models.PositiveIntegerField(verbose_name="Descarte")

    class Meta:
        verbose_name = "Registro de produccion"
        verbose_name_plural = "Registros de produccion"
        ordering = ("-date",)

    def __str__(self) -> str:
        return f"{self.date:%Y-%m-%d} · {self.production} · {self.bird_batch}"

from django.core.exceptions import ValidationError
from django.db import models


class Farm(models.Model):
    name = models.CharField("Nombre", max_length=150)

    class Meta:
        verbose_name = "Granja"
        verbose_name_plural = "Granjas"
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name


class ChickenHouse(models.Model):
    farm = models.ForeignKey(
        Farm,
        on_delete=models.CASCADE,
        related_name="chicken_houses",
        verbose_name="Granja",
    )
    name = models.CharField("Nombre", max_length=150)
    area_m2 = models.DecimalField("Area (m2)", max_digits=10, decimal_places=2)

    class Meta:
        verbose_name = "Galpon"
        verbose_name_plural = "Galpones"
        ordering = ("farm__name", "name")
        unique_together = ("farm", "name")

    def __str__(self) -> str:
        return f"{self.name} ({self.farm.name})"


class Room(models.Model):
    chicken_house = models.ForeignKey(
        ChickenHouse,
        on_delete=models.CASCADE,
        related_name="rooms",
        verbose_name="Galpon",
    )
    name = models.CharField("Nombre", max_length=150)
    area_m2 = models.DecimalField("Area (m2)", max_digits=10, decimal_places=2)

    class Meta:
        verbose_name = "Salon"
        verbose_name_plural = "Salones"
        ordering = ("chicken_house__farm__name", "chicken_house__name", "name")
        unique_together = ("chicken_house", "name")

    def __str__(self) -> str:
        return f"{self.name} ({self.chicken_house.name})"


class BirdBatch(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Activo"
        INACTIVE = "inactive", "Inactivo"

    farm = models.ForeignKey(
        Farm,
        on_delete=models.CASCADE,
        related_name="bird_batches",
        verbose_name="Granja",
    )
    status = models.CharField(
        "Estado", max_length=8, choices=Status.choices, default=Status.ACTIVE
    )
    birth_date = models.DateField("Fecha de nacimiento")
    initial_quantity = models.PositiveIntegerField("Cantidad inicial")
    breed = models.CharField("Raza", max_length=150)

    class Meta:
        verbose_name = "Lote de aves"
        verbose_name_plural = "Lotes de aves"
        ordering = ("-birth_date", "farm__name")

    def __str__(self) -> str:
        return f"Lote #{self.pk} - {self.farm.name}"


class BirdBatchRoomAllocation(models.Model):
    bird_batch = models.ForeignKey(
        BirdBatch,
        on_delete=models.CASCADE,
        related_name="allocations",
        verbose_name="Lote",
    )
    room = models.ForeignKey(
        Room,
        on_delete=models.CASCADE,
        related_name="allocations",
        verbose_name="Salon",
    )
    quantity = models.PositiveIntegerField("Cantidad")

    class Meta:
        unique_together = ("bird_batch", "room")
        verbose_name = "Asignacion de lote"
        verbose_name_plural = "Asignaciones de lotes"

    def clean(self) -> None:
        super().clean()
        if not self.room_id or not self.bird_batch_id:
            return

        room_farm_id = self.room.chicken_house.farm_id
        batch_farm_id = self.bird_batch.farm_id

        if room_farm_id != batch_farm_id:
            raise ValidationError("El salon seleccionado debe pertenecer a la misma granja del lote.")

        if self.quantity <= 0:
            raise ValidationError("La cantidad debe ser mayor que cero.")

    def __str__(self) -> str:
        return f"{self.bird_batch} -> {self.room} ({self.quantity})"

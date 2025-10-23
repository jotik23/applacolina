from django.core.exceptions import ValidationError
from django.db import models


class Farm(models.Model):
    name = models.CharField(max_length=150)

    class Meta:
        verbose_name = "Farm"
        verbose_name_plural = "Farms"
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name


class ChickenHouse(models.Model):
    farm = models.ForeignKey(
        Farm,
        on_delete=models.CASCADE,
        related_name="chicken_houses",
    )
    name = models.CharField(max_length=150)
    area_m2 = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        verbose_name = "Chicken House"
        verbose_name_plural = "Chicken Houses"
        ordering = ("farm__name", "name")
        unique_together = ("farm", "name")

    def __str__(self) -> str:
        return f"{self.name} ({self.farm.name})"


class Room(models.Model):
    chicken_house = models.ForeignKey(
        ChickenHouse,
        on_delete=models.CASCADE,
        related_name="rooms",
    )
    name = models.CharField(max_length=150)
    area_m2 = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
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
    )
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.ACTIVE)
    birth_date = models.DateField()
    initial_quantity = models.PositiveIntegerField()
    breed = models.CharField(max_length=150)

    class Meta:
        ordering = ("-birth_date", "farm__name")

    def __str__(self) -> str:
        return f"Lote #{self.pk} - {self.farm.name}"


class BirdBatchRoomAllocation(models.Model):
    bird_batch = models.ForeignKey(
        BirdBatch,
        on_delete=models.CASCADE,
        related_name="allocations",
    )
    room = models.ForeignKey(
        Room,
        on_delete=models.CASCADE,
        related_name="allocations",
    )
    quantity = models.PositiveIntegerField()

    class Meta:
        unique_together = ("bird_batch", "room")
        verbose_name = "Bird Batch Allocation"
        verbose_name_plural = "Bird Batch Allocations"

    def clean(self) -> None:
        super().clean()
        if not self.room_id or not self.bird_batch_id:
            return

        room_farm_id = self.room.chicken_house.farm_id
        batch_farm_id = self.bird_batch.farm_id

        if room_farm_id != batch_farm_id:
            raise ValidationError("The selected room must belong to the same farm as the bird batch.")

        if self.quantity <= 0:
            raise ValidationError("Quantity must be greater than zero.")

    def __str__(self) -> str:
        return f"{self.bird_batch} -> {self.room} ({self.quantity})"

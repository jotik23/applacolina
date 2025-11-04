from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q, Sum
from django.utils import timezone


class Farm(models.Model):
    name = models.CharField("Nombre", max_length=150)

    class Meta:
        verbose_name = "Granja"
        verbose_name_plural = "Granjas"
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name

    @property
    def area_m2(self) -> Decimal:
        """Aggregate the farm area from all related rooms."""
        if hasattr(self, "_prefetched_objects_cache") and "chicken_houses" in self._prefetched_objects_cache:
            total_area = Decimal("0")
            for chicken_house in self.chicken_houses.all():
                total_area += chicken_house.area_m2
            return total_area

        aggregated_area = self.chicken_houses.aggregate(total=Sum("rooms__area_m2"))
        return aggregated_area.get("total") or Decimal("0")


class ChickenHouse(models.Model):
    farm = models.ForeignKey(
        Farm,
        on_delete=models.CASCADE,
        related_name="chicken_houses",
        verbose_name="Granja",
    )
    name = models.CharField("Nombre", max_length=150)

    class Meta:
        verbose_name = "Galpon"
        verbose_name_plural = "Galpones"
        ordering = ("farm__name", "name")
        unique_together = ("farm", "name")

    def __str__(self) -> str:
        return f"{self.farm.name} - {self.name}"

    @property
    def area_m2(self) -> Decimal:
        """Aggregate the barn area from its rooms."""
        if hasattr(self, "_prefetched_objects_cache") and "rooms" in self._prefetched_objects_cache:
            total_area = Decimal("0")
            for room in self.rooms.all():
                total_area += room.area_m2
            return total_area

        aggregated_area = self.rooms.aggregate(total=Sum("area_m2"))
        return aggregated_area.get("total") or Decimal("0")


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
        farm_name = self.chicken_house.farm.name
        chicken_house_name = self.chicken_house.name
        return f"{farm_name} - {chicken_house_name} - {self.name}"


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
    average_egg_weight = models.DecimalField(
        verbose_name="Peso promedio huevo (g)",
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    recorded_at = models.DateTimeField("Registrado en", default=timezone.now, editable=False)
    updated_at = models.DateTimeField("Actualizado en", auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="production_records_created",
        null=True,
        blank=True,
        verbose_name="Registrado por",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="production_records_updated",
        null=True,
        blank=True,
        verbose_name="Última modificación por",
    )

    class Meta:
        verbose_name = "Registro de produccion"
        verbose_name_plural = "Registros de produccion"
        ordering = ("-date",)
        constraints = [
            models.UniqueConstraint(
                fields=("bird_batch", "date"),
                name="uniq_production_record_per_batch_date",
            )
        ]

    def __str__(self) -> str:
        return f"{self.date:%Y-%m-%d} · {self.production} · {self.bird_batch}"

    def recompute_totals_from_rooms(self, *, save: bool = True) -> "ProductionRecord":
        """Refresh aggregate fields using the related room records."""
        aggregates = self.room_records.aggregate(
            total_production=Sum("production"),
            total_consumption=Sum("consumption"),
            total_mortality=Sum("mortality"),
            total_discard=Sum("discard"),
        )

        self.production = aggregates.get("total_production") or Decimal("0")
        self.consumption = aggregates.get("total_consumption") or Decimal("0")
        self.mortality = int(aggregates.get("total_mortality") or 0)
        self.discard = int(aggregates.get("total_discard") or 0)

        if save:
            update_fields = ("production", "consumption", "mortality", "discard", "updated_at")
            self.save(update_fields=update_fields)
        return self

    @property
    def room_count(self) -> int:
        return self.room_records.count()

    @property
    def room_totals(self) -> dict[str, Decimal]:
        aggregates = self.room_records.aggregate(
            total_production=Sum("production"),
            total_consumption=Sum("consumption"),
            total_mortality=Sum("mortality"),
            total_discard=Sum("discard"),
        )
        return {
            "production": aggregates.get("total_production") or Decimal("0"),
            "consumption": aggregates.get("total_consumption") or Decimal("0"),
            "mortality": int(aggregates.get("total_mortality") or 0),
            "discard": int(aggregates.get("total_discard") or 0),
        }


class ProductionRoomRecord(models.Model):
    production_record = models.ForeignKey(
        ProductionRecord,
        on_delete=models.CASCADE,
        related_name="room_records",
        verbose_name="Registro de producción",
    )
    room = models.ForeignKey(
        Room,
        on_delete=models.CASCADE,
        related_name="production_records",
        verbose_name="Salón",
    )
    production = models.DecimalField(
        verbose_name="Producción",
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
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Registro de producción por salón"
        verbose_name_plural = "Registros de producción por salón"
        ordering = ("room__chicken_house__name", "room__name")
        constraints = [
            models.UniqueConstraint(
                fields=("production_record", "room"),
                name="uniq_room_production_record",
            )
        ]

    def __str__(self) -> str:
        return f"{self.production_record.date:%Y-%m-%d} · {self.room}"


class WeightSampleSession(models.Model):
    """Daily weight capture for a specific room."""

    date = models.DateField("Fecha")
    room = models.ForeignKey(
        Room,
        on_delete=models.CASCADE,
        related_name="weight_sample_sessions",
        verbose_name="Salón",
    )
    production_record = models.ForeignKey(
        ProductionRecord,
        on_delete=models.SET_NULL,
        related_name="weight_sample_sessions",
        verbose_name="Registro de producción",
        null=True,
        blank=True,
    )
    production_room_record = models.ForeignKey(
        'production.ProductionRoomRecord',
        on_delete=models.SET_NULL,
        related_name='weight_sample_sessions',
        verbose_name='Registro de producción por salón',
        null=True,
        blank=True,
    )
    task_assignment = models.ForeignKey(
        "task_manager.TaskAssignment",
        on_delete=models.SET_NULL,
        related_name="weight_sessions",
        verbose_name="Asignación de tarea",
        null=True,
        blank=True,
    )
    unit = models.CharField("Unidad", max_length=16, default="g")
    tolerance_percent = models.PositiveSmallIntegerField(
        "Tolerancia uniformidad (%)",
        default=10,
    )
    minimum_sample = models.PositiveSmallIntegerField(
        "Muestra mínima sugerida",
        default=30,
    )
    birds = models.PositiveIntegerField("Aves en salón", null=True, blank=True)
    sample_size = models.PositiveIntegerField("Tamaño de muestra", default=0)
    average_grams = models.DecimalField(
        "Peso promedio (g)",
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )
    variance_grams = models.DecimalField(
        "Varianza (g²)",
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    min_grams = models.DecimalField(
        "Peso mínimo (g)",
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )
    max_grams = models.DecimalField(
        "Peso máximo (g)",
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )
    uniformity_percent = models.DecimalField(
        "Uniformidad (%)",
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
    )
    within_tolerance = models.PositiveIntegerField(
        "Muestras dentro de la tolerancia",
        default=0,
    )
    submitted_at = models.DateTimeField("Enviado en", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="weight_sample_sessions_created",
        verbose_name="Registrado por",
        null=True,
        blank=True,
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="weight_sample_sessions_updated",
        verbose_name="Actualizado por",
        null=True,
        blank=True,
    )

    class Meta:
        verbose_name = "Sesión de pesaje"
        verbose_name_plural = "Sesiones de pesaje"
        ordering = ("-date", "-updated_at")
        constraints = [
            models.UniqueConstraint(
                fields=("room", "task_assignment"),
                name="uniq_weight_session_assignment_room",
                condition=Q(task_assignment__isnull=False),
            ),
            models.UniqueConstraint(
                fields=("date", "room"),
                name="uniq_weight_session_date_room_unassigned",
                condition=Q(task_assignment__isnull=True),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.date:%Y-%m-%d} · {self.room}"


class WeightSample(models.Model):
    """Individual weight capture belonging to a session."""

    session = models.ForeignKey(
        WeightSampleSession,
        on_delete=models.CASCADE,
        related_name="samples",
        verbose_name="Sesión",
    )
    grams = models.DecimalField(
        "Peso (g)",
        max_digits=8,
        decimal_places=2,
    )
    recorded_at = models.DateTimeField("Registrado en", default=timezone.now)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="weight_samples_recorded",
        verbose_name="Registrado por",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Peso registrado"
        verbose_name_plural = "Pesos registrados"
        ordering = ("created_at",)

    def __str__(self) -> str:
        return f"{self.session} · {self.grams} g"

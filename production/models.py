from datetime import date
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q, Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

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
    egg_destination_farm = models.ForeignKey(
        Farm,
        on_delete=models.PROTECT,
        related_name="egg_destination_chicken_houses",
        verbose_name="Granja destino del huevo",
        blank=True,
        null=True,
        help_text="Selecciona la granja a la que se debe enviar la producción de este galpón.",
    )

    class Meta:
        verbose_name = "Galpon"
        verbose_name_plural = "Galpones"
        ordering = ("farm__name", "name")
        unique_together = ("farm", "name")

    def __str__(self) -> str:
        return f"{self.farm.name} - {self.name}"

    def save(self, *args, **kwargs) -> None:
        if self.egg_destination_farm_id is None and self.farm_id:
            self.egg_destination_farm_id = self.farm_id
            update_fields = kwargs.get("update_fields")
            if update_fields:
                fields = list(update_fields)
                if "egg_destination_farm" not in fields:
                    fields.append("egg_destination_farm")
                kwargs["update_fields"] = fields
        super().save(*args, **kwargs)

    @property
    def destination_farm(self) -> Farm:
        return self.egg_destination_farm or self.farm

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


class BreedReference(models.Model):
    name = models.CharField("Nombre", max_length=150, unique=True)

    class Meta:
        verbose_name = "Raza"
        verbose_name_plural = "Razas"
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name


class BreedWeeklyGuide(models.Model):
    WEEK_MAX = 100

    breed = models.ForeignKey(
        BreedReference,
        on_delete=models.CASCADE,
        related_name="weekly_guides",
        verbose_name="Raza",
    )
    week = models.PositiveSmallIntegerField(
        "Semana (vida)",
        validators=[MinValueValidator(1), MaxValueValidator(WEEK_MAX)],
    )
    posture_percentage = models.DecimalField(
        "% postura",
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
    )
    haa = models.DecimalField(
        "H.A.A",
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
    )
    egg_weight_g = models.DecimalField(
        "Peso huevo (g)",
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
    )
    grams_per_bird = models.DecimalField(
        "Gr/ave/día",
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
    )
    cumulative_feed = models.DecimalField(
        "Consumo alimento acumulado (kg)",
        max_digits=9,
        decimal_places=2,
        null=True,
        blank=True,
    )
    conversion_index = models.DecimalField(
        "Índice de conversión",
        max_digits=6,
        decimal_places=3,
        null=True,
        blank=True,
    )
    cumulative_conversion = models.DecimalField(
        "Conversión acumulada",
        max_digits=6,
        decimal_places=3,
        null=True,
        blank=True,
    )
    weekly_mortality_percentage = models.DecimalField(
        "% mortalidad semanal",
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
    )
    body_weight_g = models.DecimalField(
        "Peso corporal (g)",
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
    )

    class Meta:
        verbose_name = "Guía semanal de raza"
        verbose_name_plural = "Guías semanales de raza"
        constraints = [
            models.UniqueConstraint(
                fields=["breed", "week"], name="unique_breed_week_reference"
            )
        ]
        ordering = ("breed__name", "week")

    def __str__(self) -> str:
        return f"{self.breed.name} · Semana {self.week}"


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
    breed = models.ForeignKey(
        BreedReference,
        on_delete=models.PROTECT,
        related_name="batches",
        verbose_name="Raza",
    )

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


class EggType(models.TextChoices):
    JUMBO = "jumbo", "Jumbo"
    TRIPLE_A = "aaa", "AAA"
    DOUBLE_A = "aa", "AA"
    SINGLE_A = "a", "A"
    B = "b", "B"
    C = "c", "C"
    D = "d", "D"


class EggClassificationBatch(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pendiente"
        CONFIRMED = "confirmed", "Recibido"
        CLASSIFIED = "classified", "Clasificado"

    class TransportStatus(models.TextChoices):
        PENDING = "pending", _("Pendiente de transporte")
        AUTHORIZED = "authorized", _("Autorizado")
        IN_TRANSIT = "in_transit", _("En transporte")
        VERIFICATION = "verification", _("En verificación")
        VERIFIED = "verified", _("Verificado")

    production_record = models.OneToOneField(
        ProductionRecord,
        on_delete=models.CASCADE,
        related_name="egg_classification",
        verbose_name="Registro de producción",
    )
    bird_batch = models.ForeignKey(
        BirdBatch,
        on_delete=models.CASCADE,
        related_name="egg_classifications",
        verbose_name="Lote",
    )
    reported_cartons = models.DecimalField(
        "Cartones reportados",
        max_digits=10,
        decimal_places=2,
    )
    received_cartons = models.DecimalField(
        "Cartones recibidos",
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )
    notes = models.TextField("Notas para conciliación", blank=True)
    status = models.CharField(
        "Estado",
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
    )
    confirmed_at = models.DateTimeField("Confirmado en", null=True, blank=True)
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="egg_batches_confirmed",
        verbose_name="Confirmado por",
        null=True,
        blank=True,
    )
    classified_at = models.DateTimeField("Clasificado en", null=True, blank=True)
    classified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="egg_batches_classified",
        verbose_name="Clasificado por",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    transport_status = models.CharField(
        "Estado de transporte",
        max_length=16,
        choices=TransportStatus.choices,
        default=TransportStatus.PENDING,
    )
    transport_destination_farm = models.ForeignKey(
        Farm,
        on_delete=models.PROTECT,
        related_name="egg_transport_batches",
        verbose_name="Granja destino",
        null=True,
        blank=True,
    )
    transport_transporter = models.ForeignKey(
        "personal.UserProfile",
        on_delete=models.SET_NULL,
        related_name="egg_transport_assignments",
        verbose_name="Transportador",
        null=True,
        blank=True,
    )
    transport_expected_date = models.DateField("Fecha estimada de transporte", null=True, blank=True)
    transport_authorized_at = models.DateTimeField("Autorizado en", null=True, blank=True)
    transport_authorized_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="egg_transport_authorizations",
        verbose_name="Autorizado por",
        null=True,
        blank=True,
    )
    transport_progress_step = models.CharField("Paso de transporte", max_length=32, blank=True)
    transport_verified_cartons = models.DecimalField(
        "Cartones verificados",
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )
    transport_verified_at = models.DateTimeField("Verificado en", null=True, blank=True)
    transport_verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="egg_transport_verifications",
        verbose_name="Verificado por",
        null=True,
        blank=True,
    )
    transport_confirmed_cartons = models.DecimalField(
        "Cartones confirmados por transportador",
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )
    transport_confirmed_at = models.DateTimeField("Confirmado por transportador en", null=True, blank=True)
    transport_confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="egg_transport_confirmations",
        verbose_name="Confirmado por transportador",
        null=True,
        blank=True,
    )

    class Meta:
        verbose_name = "Lote de clasificación de huevo"
        verbose_name_plural = "Lotes de clasificación de huevo"
        ordering = ("-production_record__date", "-created_at")
        permissions = [
            (
                "access_egg_inventory",
                "Puede acceder a la vista de Clasificación e inventario de huevo",
            ),
            (
                "confirm_egg_batch_receipt",
                "Puede confirmar cartones recibidos en un lote de clasificación",
            ),
            (
                "record_egg_classification",
                "Puede registrar resultados de clasificación de huevo",
            ),
            (
                "revert_egg_classification_session",
                "Puede revertir iteraciones de clasificación registradas",
            ),
            (
                "reset_egg_classification_day",
                "Puede restablecer completamente el progreso de un lote de clasificación",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.production_record.date:%Y-%m-%d} · {self.bird_batch}"

    @property
    def farm(self) -> Farm:
        return self.bird_batch.farm

    @property
    def production_date(self) -> date:
        return self.production_record.date

    @property
    def received_difference(self) -> Decimal:
        if self.received_cartons is None:
            return Decimal("0")
        return Decimal(self.received_cartons) - Decimal(self.reported_cartons)

    @property
    def classified_total(self) -> Decimal:
        cached = getattr(self, "_classified_total_cache", None)
        if cached is not None:
            return cached
        aggregates = self.classification_entries.aggregate(total=Sum("cartons"))
        total = aggregates.get("total") or Decimal("0")
        self._classified_total_cache = total
        return total

    @property
    def pending_cartons(self) -> Decimal:
        if self.received_cartons is None:
            return Decimal(self.reported_cartons)
        return Decimal(self.received_cartons) - self.classified_total

    def refresh_from_db(self, using=None, fields=None, **kwargs) -> None:
        super().refresh_from_db(using=using, fields=fields, **kwargs)
        if hasattr(self, "_classified_total_cache"):
            delattr(self, "_classified_total_cache")


class EggClassificationSession(models.Model):
    batch = models.ForeignKey(
        EggClassificationBatch,
        on_delete=models.CASCADE,
        related_name="classification_sessions",
        verbose_name="Lote de clasificación",
    )
    classified_at = models.DateTimeField("Clasificado en", default=timezone.now)
    classified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="egg_classification_sessions",
        verbose_name="Clasificado por",
        null=True,
        blank=True,
    )
    notes = models.CharField("Notas", max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Sesión de clasificación de huevo"
        verbose_name_plural = "Sesiones de clasificación de huevo"
        ordering = ("-classified_at", "-created_at")

    def __str__(self) -> str:
        timestamp = timezone.localtime(self.classified_at)
        return f"{self.batch} · {timestamp:%Y-%m-%d %H:%M}"


class EggClassificationEntry(models.Model):
    batch = models.ForeignKey(
        EggClassificationBatch,
        on_delete=models.CASCADE,
        related_name="classification_entries",
        verbose_name="Lote de clasificación",
    )
    session = models.ForeignKey(
        EggClassificationSession,
        on_delete=models.CASCADE,
        related_name="entries",
        verbose_name="Sesión de clasificación",
    )
    egg_type = models.CharField("Tipo de huevo", max_length=8, choices=EggType.choices)
    cartons = models.DecimalField("Cartones", max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Resultado de clasificación"
        verbose_name_plural = "Resultados de clasificación"
        unique_together = ("session", "egg_type")
        ordering = ("session", "egg_type")

    def __str__(self) -> str:
        egg_label = self.get_egg_type_display()
        return f"{self.batch} · {egg_label} ({self.cartons})"


class EggDispatchDestination(models.TextChoices):
    TIERRALTA = "tierralta", "Tierralta"
    MONTERIA = "monteria", "Montería"
    BAJO_CAUCA = "bajo_cauca", "Bajo Cauca"


class EggDispatch(models.Model):
    date = models.DateField("Fecha de despacho")
    destination = models.CharField(
        "Destino",
        max_length=32,
        choices=EggDispatchDestination.choices,
    )
    driver = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="egg_dispatches_driven",
        verbose_name="Conductor",
    )
    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="egg_dispatches_sold",
        verbose_name="Vendedor responsable",
    )
    notes = models.TextField("Notas", blank=True)
    total_cartons = models.DecimalField("Total cartones", max_digits=10, decimal_places=2, default=Decimal("0"))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="egg_dispatches_created",
        verbose_name="Registrado por",
        null=True,
        blank=True,
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="egg_dispatches_updated",
        verbose_name="Actualizado por",
        null=True,
        blank=True,
    )

    class Meta:
        verbose_name = "Despacho de huevo"
        verbose_name_plural = "Despachos de huevo"
        ordering = ("-date", "-created_at")

    def __str__(self) -> str:
        destination = self.get_destination_display()
        return f"{self.date:%Y-%m-%d} · {destination}"

    @property
    def destination_label(self) -> str:
        return self.get_destination_display()

    @property
    def driver_name(self) -> str:
        driver = getattr(self, "driver", None)
        if not driver:
            return ""
        return driver.get_full_name() or driver.get_username()

    @property
    def seller_name(self) -> str:
        seller = getattr(self, "seller", None)
        if not seller:
            return ""
        return seller.get_full_name() or seller.get_username()


class EggDispatchItem(models.Model):
    dispatch = models.ForeignKey(
        EggDispatch,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name="Despacho",
    )
    egg_type = models.CharField("Tipo de huevo", max_length=8, choices=EggType.choices)
    cartons = models.DecimalField("Cartones despachados", max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Detalle de despacho"
        verbose_name_plural = "Detalles de despacho"
        unique_together = ("dispatch", "egg_type")
        ordering = ("egg_type",)

    def __str__(self) -> str:
        return f"{self.dispatch} · {self.get_egg_type_display()} ({self.cartons})"


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

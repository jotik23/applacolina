from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from administration.models import Product
from production.models import ChickenHouse, Farm


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class InventoryScope(models.TextChoices):
    COMPANY = "company", "General"
    FARM = "farm", "Granja"
    CHICKEN_HOUSE = "chicken_house", "Galpón"


class ProductInventoryBalance(TimeStampedModel):
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="inventory_balances",
    )
    scope = models.CharField(
        max_length=20,
        choices=InventoryScope.choices,
        default=InventoryScope.COMPANY,
    )
    farm = models.ForeignKey(
        Farm,
        on_delete=models.CASCADE,
        related_name="inventory_balances",
        null=True,
        blank=True,
    )
    chicken_house = models.ForeignKey(
        ChickenHouse,
        on_delete=models.CASCADE,
        related_name="inventory_balances",
        null=True,
        blank=True,
    )
    quantity = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    class Meta:
        verbose_name = "Saldo de inventario"
        verbose_name_plural = "Saldos de inventario"
        unique_together = ("product", "scope", "farm", "chicken_house")

    def __str__(self) -> str:
        return f"{self.product} · {self.scope_label} · {self.quantity}"

    @property
    def scope_label(self) -> str:
        if self.scope == InventoryScope.CHICKEN_HOUSE and self.chicken_house:
            return f"Galpón · {self.chicken_house}"
        if self.scope == InventoryScope.FARM and self.farm:
            return f"Granja · {self.farm}"
        return "General"

    def clean(self) -> None:
        super().clean()
        if self.scope == InventoryScope.COMPANY:
            self.farm = None
            self.chicken_house = None
        elif self.scope == InventoryScope.FARM:
            if not self.farm:
                raise ValidationError("Debes seleccionar la granja para este saldo.")
            self.chicken_house = None
        elif self.scope == InventoryScope.CHICKEN_HOUSE:
            if not self.chicken_house:
                raise ValidationError("Debes seleccionar el galpón para este saldo.")
            if not self.farm:
                self.farm = self.chicken_house.farm


class ProductInventoryEntry(TimeStampedModel):
    class EntryType(models.TextChoices):
        RECEIPT = "receipt", "Ingreso"
        CONSUMPTION = "consumption", "Consumo automático"
        MANUAL_CONSUMPTION = "manual_consumption", "Consumo manual"
        RESET = "reset", "Reseteo"
        ADJUSTMENT = "adjustment", "Ajuste"

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="inventory_entries",
    )
    entry_type = models.CharField(max_length=32, choices=EntryType.choices)
    scope = models.CharField(max_length=20, choices=InventoryScope.choices)
    farm = models.ForeignKey(
        Farm,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="inventory_entries",
    )
    chicken_house = models.ForeignKey(
        ChickenHouse,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="inventory_entries",
    )
    effective_date = models.DateField(default=timezone.now)
    quantity_in = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    quantity_out = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    balance_after = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inventory_entries_recorded",
    )
    executed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inventory_entries_executed",
    )
    notes = models.TextField(blank=True)
    data = models.JSONField(default=dict, blank=True)
    reference_type = models.CharField(max_length=80, blank=True)
    reference_id = models.PositiveIntegerField(null=True, blank=True)
    reference_content_type = models.ForeignKey(
        ContentType,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    reference_object = GenericForeignKey("reference_content_type", "reference_id")

    class Meta:
        verbose_name = "Movimiento de inventario"
        verbose_name_plural = "Movimientos de inventario"
        ordering = ("-effective_date", "-created_at")

    def __str__(self) -> str:
        return f"{self.product} · {self.get_entry_type_display()} · {self.scope_label}"

    @property
    def scope_label(self) -> str:
        if self.scope == InventoryScope.CHICKEN_HOUSE and self.chicken_house:
            return f"Galpón · {self.chicken_house}"
        if self.scope == InventoryScope.FARM and self.farm:
            return f"Granja · {self.farm}"
        return "General"

    def set_reference(self, instance: models.Model | None) -> None:
        if not instance:
            self.reference_id = None
            self.reference_type = ""
            self.reference_content_type = None
            return
        self.reference_id = instance.pk
        self.reference_type = f"{instance._meta.app_label}.{instance._meta.model_name}"
        self.reference_content_type = ContentType.objects.get_for_model(instance, for_concrete_model=False)

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self.effective_date:
            self.effective_date = timezone.now().date()
        super().save(*args, **kwargs)


class ProductConsumptionConfig(TimeStampedModel):
    class Scope(models.TextChoices):
        FARM = "farm", "Granja"
        CHICKEN_HOUSE = "chicken_house", "Galpón"

    scope = models.CharField(
        max_length=20,
        choices=Scope.choices,
        default=Scope.CHICKEN_HOUSE,
    )
    farm = models.ForeignKey(
        Farm,
        on_delete=models.CASCADE,
        related_name="product_consumption_configs",
        null=True,
        blank=True,
    )
    chicken_house = models.ForeignKey(
        ChickenHouse,
        on_delete=models.CASCADE,
        related_name="product_consumption_configs",
        null=True,
        blank=True,
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="consumption_configs",
    )
    start_date = models.DateField()
    notes = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="product_consumption_configs",
    )

    class Meta:
        verbose_name = "Configuración de consumo por salón"
        verbose_name_plural = "Configuraciones de consumo por salón"
        ordering = ("-start_date", "scope", "chicken_house__name", "farm__name")

    def __str__(self) -> str:
        label = self.chicken_house or self.farm or "Ámbito"
        return f"{label} → {self.product}"

    def clean(self) -> None:
        super().clean()
        if not self.scope:
            raise ValidationError("Debes seleccionar el ámbito.")
        if self.scope == self.Scope.FARM:
            if not self.farm:
                raise ValidationError("Selecciona la granja para esta configuración.")
            self.chicken_house = None
        elif self.scope == self.Scope.CHICKEN_HOUSE:
            if not self.chicken_house:
                raise ValidationError("Selecciona el galpón para esta configuración.")
            if not self.farm:
                self.farm = self.chicken_house.farm
        else:
            raise ValidationError("Ámbito inválido.")

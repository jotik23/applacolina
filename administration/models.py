from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from production.models import ChickenHouse, Farm


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Supplier(TimeStampedModel):
    class TaxRegime(models.TextChoices):
        GENERAL = "general", "Régimen general"
        SIMPLIFIED = "simplificado", "Régimen simplificado"
        SPECIAL = "especial", "Régimen especial"

    class AccountType(models.TextChoices):
        CHECKING = "checking", "Cuenta corriente"
        SAVINGS = "savings", "Cuenta de ahorros"
        OTHER = "other", "Otro"

    name = models.CharField("Nombre comercial", max_length=255)
    tax_id = models.CharField("NIT", max_length=50, unique=True)
    tax_regime = models.CharField(
        "Régimen",
        max_length=20,
        choices=TaxRegime.choices,
        default=TaxRegime.GENERAL,
    )
    payment_terms_days = models.PositiveSmallIntegerField(
        "Plazo de pago (días)",
        default=30,
        validators=[MaxValueValidator(365)],
    )
    is_active = models.BooleanField("Activo", default=True)
    contact_name = models.CharField("Contacto", max_length=150, blank=True)
    contact_email = models.EmailField("Correo de contacto", blank=True)
    contact_phone = models.CharField("Teléfono", max_length=50, blank=True)
    address = models.CharField("Dirección", max_length=255, blank=True)
    city = models.CharField("Ciudad", max_length=120, blank=True)
    bank_name = models.CharField("Banco", max_length=150, blank=True)
    bank_account_type = models.CharField(
        "Tipo de cuenta",
        max_length=20,
        choices=AccountType.choices,
        default=AccountType.CHECKING,
    )
    bank_account_number = models.CharField("Número de cuenta", max_length=64, blank=True)
    requires_vat_retention = models.BooleanField("Retención IVA", default=False)
    requires_ica_retention = models.BooleanField("Retención ICA", default=False)
    requires_rtefte = models.BooleanField("Retefuente", default=False)
    notes = models.TextField("Notas internas", blank=True)

    class Meta:
        verbose_name = "Proveedor"
        verbose_name_plural = "Proveedores"
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name


class PurchasingExpenseType(TimeStampedModel):
    class Scope(models.TextChoices):
        FARM = "farm", "Granjas"
        PLANT = "plant", "Planta"
        OFFICE = "office", "Oficinas"
        LOGISTICS = "logistics", "Logística"

    code = models.CharField("Código", max_length=30, unique=True)
    name = models.CharField("Nombre", max_length=200)
    scope = models.CharField("Ámbito", max_length=20, choices=Scope.choices, default=Scope.FARM)
    description = models.TextField("Descripción", blank=True)
    iva_rate = models.DecimalField(
        "IVA (%)",
        max_digits=5,
        decimal_places=2,
        default=Decimal("19.00"),
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    withholding_rate = models.DecimalField(
        "Retención (%)",
        max_digits=5,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    requires_invoice = models.BooleanField("Exige factura", default=True)
    requires_supporting_docs = models.BooleanField("Documentos obligatorios", default=False)
    mandatory_documents = models.CharField("Documentos solicitados", max_length=255, blank=True)
    is_active = models.BooleanField("Activo", default=True)

    class Meta:
        verbose_name = "Tipo de gasto"
        verbose_name_plural = "Tipos de gasto"
        ordering = ("name",)

    def __str__(self) -> str:
        return f"{self.name} ({self.code})"


class ExpenseTypeApprovalRule(TimeStampedModel):
    expense_type = models.ForeignKey(
        PurchasingExpenseType,
        on_delete=models.CASCADE,
        related_name="approval_rules",
        verbose_name="Tipo de gasto",
    )
    sequence = models.PositiveSmallIntegerField("Secuencia")
    name = models.CharField("Nombre del paso", max_length=150)
    approver = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="expense_type_approvals",
        verbose_name="Aprobador",
    )

    class Meta:
        verbose_name = "Regla de aprobación"
        verbose_name_plural = "Reglas de aprobación"
        ordering = ("expense_type", "sequence")
        unique_together = ("expense_type", "sequence")

    def __str__(self) -> str:
        return f"{self.expense_type.code} · {self.sequence} - {self.name}"


class CostCenterConfig(TimeStampedModel):
    class AllocationMethod(models.TextChoices):
        MANUAL = "manual", "Manual"
        AREA = "area", "Por área"
        PRODUCTION = "production", "Producción"
        HEADCOUNT = "headcount", "Headcount"

    expense_type = models.ForeignKey(
        PurchasingExpenseType,
        on_delete=models.PROTECT,
        related_name="cost_centers",
        verbose_name="Tipo de gasto",
    )
    name = models.CharField("Nombre", max_length=150)
    scope = models.CharField("Ámbito", max_length=20, choices=PurchasingExpenseType.Scope.choices)
    allocation_method = models.CharField(
        "Método de asignación",
        max_length=20,
        choices=AllocationMethod.choices,
        default=AllocationMethod.MANUAL,
    )
    percentage = models.DecimalField(
        "Porcentaje",
        max_digits=5,
        decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    valid_from = models.DateField("Válido desde")
    valid_until = models.DateField("Válido hasta", blank=True, null=True)
    is_required = models.BooleanField("Obligatorio", default=False)
    farm = models.ForeignKey(
        Farm,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="cost_centers",
        verbose_name="Granja",
    )
    chicken_house = models.ForeignKey(
        ChickenHouse,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="cost_centers",
        verbose_name="Galpón",
    )
    notes = models.TextField("Notas", blank=True)
    is_active = models.BooleanField("Activo", default=True)

    class Meta:
        verbose_name = "Configuración de centro de costo"
        verbose_name_plural = "Configuraciones de centros de costo"
        ordering = ("-valid_from", "name")

    def __str__(self) -> str:
        return self.name

    @property
    def delete_protected_message(self) -> str:
        return "No es posible eliminar este centro de costo porque tiene movimientos asociados."


class PurchaseRequest(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "borrador", "Borrador"
        APPROVAL = "aprobacion", "En aprobación"
        ORDERED = "ordenado", "Orden emitida"
        RECEPTION = "recepcion", "Recepción"
        INVOICE = "factura", "Factura"
        PAYMENT = "pago", "Pago"
        ARCHIVED = "archivada", "Archivada"

    timeline_code = models.CharField("Código", max_length=40, unique=True)
    name = models.CharField("Nombre", max_length=200)
    description = models.TextField("Descripción", blank=True)
    requester = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="purchase_requests",
        verbose_name="Solicitante",
    )
    supplier = models.ForeignKey(
        Supplier,
        on_delete=models.PROTECT,
        related_name="purchase_requests",
        verbose_name="Proveedor",
    )
    expense_type = models.ForeignKey(
        PurchasingExpenseType,
        on_delete=models.PROTECT,
        related_name="purchase_requests",
        verbose_name="Tipo de gasto",
    )
    cost_center = models.ForeignKey(
        CostCenterConfig,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="purchase_requests",
        verbose_name="Centro de costo",
    )
    status = models.CharField(
        "Estado",
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    currency = models.CharField("Moneda", max_length=3, default="COP")
    estimated_total = models.DecimalField(
        "Total estimado",
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    eta = models.DateField("ETA", blank=True, null=True)
    order_number = models.CharField("Número de orden", max_length=60, blank=True)
    order_date = models.DateField("Fecha de orden", blank=True, null=True)
    reception_notes = models.TextField("Notas de recepción", blank=True)
    invoice_number = models.CharField("Número de factura", max_length=60, blank=True)
    invoice_date = models.DateField("Fecha factura", blank=True, null=True)
    invoice_total = models.DecimalField(
        "Total factura",
        max_digits=14,
        decimal_places=2,
        blank=True,
        null=True,
    )
    payment_account = models.CharField("Cuenta de pago", max_length=120, blank=True)
    payment_date = models.DateField("Fecha de pago", blank=True, null=True)
    payment_notes = models.TextField("Notas de pago", blank=True)

    class Meta:
        verbose_name = "Solicitud de compra"
        verbose_name_plural = "Solicitudes de compra"
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.timeline_code} - {self.name}"

    def stage_status(self, stage_code: str) -> str:
        flow = [
            self.Status.DRAFT,
            self.Status.APPROVAL,
            self.Status.ORDERED,
            self.Status.RECEPTION,
            self.Status.INVOICE,
            self.Status.PAYMENT,
            self.Status.ARCHIVED,
        ]
        stage_index = {
            "request": 0,
            "order": 2,
            "reception": 3,
            "invoice": 4,
            "payment": 5,
        }
        current_index = flow.index(self.status)
        target_index = stage_index.get(stage_code, 0)
        if current_index > target_index:
            return "completed"
        if current_index == target_index:
            return "active"
        if flow[target_index] == self.Status.ARCHIVED:
            return "completed"
        if current_index + 1 == target_index:
            return "pending"
        return "locked"

    @property
    def scope_label(self) -> str:
        if self.cost_center:
            return self.cost_center.name
        return self.expense_type.name


class PurchaseItem(TimeStampedModel):
    purchase = models.ForeignKey(
        PurchaseRequest,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name="Solicitud",
    )
    description = models.CharField("Descripción", max_length=255)
    quantity = models.DecimalField(
        "Cantidad",
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )
    unit = models.CharField("Unidad", max_length=30)
    farm = models.ForeignKey(
        Farm,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="purchase_items",
        verbose_name="Granja",
    )
    chicken_house = models.ForeignKey(
        ChickenHouse,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="purchase_items",
        verbose_name="Galpón",
    )
    batch_code = models.CharField("Lote", max_length=60, blank=True)
    estimated_amount = models.DecimalField(
        "Monto estimado",
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )

    class Meta:
        verbose_name = "Item de compra"
        verbose_name_plural = "Items de compra"

    def __str__(self) -> str:
        return self.description

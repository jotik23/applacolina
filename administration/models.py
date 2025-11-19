from __future__ import annotations

from collections import OrderedDict
from decimal import Decimal
import os
from typing import ClassVar

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from production.models import ChickenHouse, EggDispatchDestination, Farm


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Product(TimeStampedModel):
    class Unit(models.TextChoices):
        BULK = "Bultos", "Bultos"
        PACK_100 = "Paquete x 100", "Paquete x 100"
        PACK_120 = "Paquete x 120", "Paquete x 120"
        UNIT = "Unidad", "Unidad"

    name = models.CharField("Nombre", max_length=150, unique=True)
    unit = models.CharField(
        "Unidad",
        max_length=60,
        choices=Unit.choices,
        default=Unit.UNIT,
    )

    class Meta:
        ordering = ("name",)
        verbose_name = "Producto"
        verbose_name_plural = "Productos"

    def __str__(self) -> str:
        return self.name


class Supplier(TimeStampedModel):
    name = models.CharField("Nombre / Razón social", max_length=255)
    tax_id = models.CharField("CC/NIT", max_length=50, unique=True)
    contact_name = models.CharField("Contacto", max_length=150, blank=True)
    contact_email = models.EmailField("Correo de contacto", blank=True)
    contact_phone = models.CharField("Teléfono", max_length=50, blank=True)
    address = models.CharField("Dirección", max_length=255, blank=True)
    city = models.CharField("Ciudad", max_length=120, blank=True)
    account_holder_id = models.CharField("Identificación titular", max_length=50, blank=True)
    account_holder_name = models.CharField("Nombre titular", max_length=255, blank=True)
    ACCOUNT_TYPE_CHOICES = (
        ("ahorros", "Ahorros"),
        ("corriente", "Corriente"),
    )
    account_type = models.CharField("Tipo de cuenta", max_length=20, choices=ACCOUNT_TYPE_CHOICES, blank=True)
    account_number = models.CharField("Número de cuenta", max_length=60, blank=True)
    bank_name = models.CharField("Banco", max_length=120, blank=True)

    class Meta:
        verbose_name = "Tercero"
        verbose_name_plural = "Terceros"
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name


class SupportDocumentType(TimeStampedModel):
    class Kind(models.TextChoices):
        EXTERNAL = "external", "Soporte externo"
        INTERNAL = "internal", "Soporte interno"

    name = models.CharField("Nombre", max_length=120, unique=True)
    kind = models.CharField("Tipo", max_length=20, choices=Kind.choices, default=Kind.EXTERNAL)
    template = models.TextField(
        "Plantilla soporte",
        blank=True,
        help_text="HTML usado para generar el soporte interno. Usa {{campo}} para valores dinámicos.",
    )

    class Meta:
        verbose_name = "Tipo de soporte"
        verbose_name_plural = "Tipos de soporte"
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name

    @property
    def requires_template(self) -> bool:
        return self.kind == self.Kind.INTERNAL


class PurchasingExpenseType(TimeStampedModel):
    name = models.CharField("Nombre", max_length=200)
    parent_category = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="child_categories",
        verbose_name="Categoría padre",
    )
    default_support_document_type = models.ForeignKey(
        SupportDocumentType,
        on_delete=models.PROTECT,
        related_name="default_for_categories",
        verbose_name="Tipo de soporte por defecto",
        null=True,
        blank=True,
    )
    iva_rate = models.DecimalField(
        "IVA (%)",
        max_digits=5,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    withholding_rate = models.DecimalField(
        "Retención (%)",
        max_digits=5,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    assumed_withholding_rate = models.DecimalField(
        "Retención asumida (%)",
        max_digits=5,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    class Meta:
        verbose_name = "Categoría de gasto"
        verbose_name_plural = "Categorías de gasto"
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name

    @property
    def approval_phase_summary(self) -> str:
        phases: list[str] = []
        for rule in self.approval_rules.select_related('approver').order_by('id'):
            label = ''
            if rule.approver:
                get_full_name = getattr(rule.approver, 'get_full_name', None)
                if callable(get_full_name):
                    label = (get_full_name() or '').strip()
                if not label:
                    label = getattr(rule.approver, 'email', '') or str(rule.approver)
            phases.append(label or 'Aprobador')
        return ", ".join(phases)


class ExpenseTypeApprovalRule(TimeStampedModel):
    expense_type = models.ForeignKey(
        PurchasingExpenseType,
        on_delete=models.CASCADE,
        related_name="approval_rules",
        verbose_name="Categoría de gasto",
    )
    approver = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="expense_type_approvals",
        verbose_name="Aprobador",
    )

    class Meta:
        verbose_name = "Regla de aprobación"
        verbose_name_plural = "Reglas de aprobación"
        ordering = ("expense_type", "id")

    def __str__(self) -> str:
        approver_label = ''
        if self.approver:
            get_full_name = getattr(self.approver, "get_full_name", None)
            if callable(get_full_name):
                approver_label = (get_full_name() or '').strip()
            if not approver_label:
                approver_label = getattr(self.approver, "email", "") or str(self.approver)
        return f"{self.expense_type.name} · {approver_label or 'Aprobador'}"


class PurchaseRequest(TimeStampedModel):
    class DeliveryCondition(models.TextChoices):
        IMMEDIATE = "immediate", "Entrega inmediata"
        SHIPPING = "shipping", "Envío posterior"

    class PaymentCondition(models.TextChoices):
        CASH = "contado", "Contado"
        CREDIT = "credito", "Crédito"
        CREDIT_PAID = "credito_pagado", "Crédito pagado"

    class PaymentMethod(models.TextChoices):
        CASH = "efectivo", "Efectivo"
        TRANSFER = "transferencia", "Transferencia"

    class PaymentSource(models.TextChoices):
        TBD = "tbd", "Por definir (TBD)"
        TREASURY = "treasury", "Tesorería"

    class AreaScope(models.TextChoices):
        COMPANY = "company", "Empresa"
        FARM = "farm", "Granja"
        CHICKEN_HOUSE = "chicken_house", "Galpón"

    class Status(models.TextChoices):
        DRAFT = "borrador", "Borrador"
        SUBMITTED = "aprobacion", "En aprobación"
        APPROVED = "aprobada", "Aprobada"
        RECEPTION = "recepcion", "Gestionar pago"
        INVOICE = "factura", "Factura"
        PAYMENT = "pago", "Pago"
        ARCHIVED = "archivada", "Archivada"

    STAGE_FLOW: ClassVar[tuple[tuple[str, str], ...]] = (
        ("draft", Status.DRAFT),
        ("approval", Status.SUBMITTED),
        ("purchasing", Status.APPROVED),
        ("payable", Status.RECEPTION),
        ("support", Status.INVOICE),
        ("accounting", Status.PAYMENT),
        ("archived", Status.ARCHIVED),
    )
    POST_PAYMENT_STATUSES: ClassVar[set[str]] = {
        Status.RECEPTION,
        Status.INVOICE,
        Status.PAYMENT,
        Status.ARCHIVED,
    }

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
    assigned_manager = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="managed_purchase_requests",
        verbose_name="Gestor asignado",
    )
    supplier = models.ForeignKey(
        Supplier,
        on_delete=models.PROTECT,
        related_name="purchase_requests",
        verbose_name="Tercero",
    )
    expense_type = models.ForeignKey(
        PurchasingExpenseType,
        on_delete=models.PROTECT,
        related_name="purchase_requests",
        verbose_name="Categoría de gasto",
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
    purchase_date = models.DateField("Fecha de compra", blank=True, null=True)
    delivery_condition = models.CharField(
        "Condiciones de entrega",
        max_length=20,
        choices=DeliveryCondition.choices,
        default=DeliveryCondition.IMMEDIATE,
    )
    delivery_terms = models.TextField("Condiciones de entrega (legacy)", blank=True, default="")
    shipping_eta = models.DateField("Fecha estimada de llegada", blank=True, null=True)
    shipping_notes = models.TextField("Notas de envío", blank=True)
    payment_condition = models.CharField(
        "Condiciones de pago",
        max_length=20,
        choices=PaymentCondition.choices,
        blank=True,
    )
    payment_method = models.CharField(
        "Medio de pago",
        max_length=20,
        choices=PaymentMethod.choices,
        blank=True,
    )
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
    payment_amount = models.DecimalField(
        "Monto a pagar",
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    payment_account = models.CharField("Cuenta de pago", max_length=120, blank=True)
    payment_date = models.DateField("Fecha de pago", blank=True, null=True)
    payment_notes = models.TextField("Notas de pago", blank=True)
    payment_source = models.CharField(
        "Origen del pago",
        max_length=20,
        choices=PaymentSource.choices,
        default=PaymentSource.TBD,
    )
    supplier_account_holder_id = models.CharField("Identificación titular (compra)", max_length=50, blank=True)
    supplier_account_holder_name = models.CharField("Nombre titular (compra)", max_length=255, blank=True)
    supplier_account_type = models.CharField(
        "Tipo de cuenta (compra)",
        max_length=20,
        choices=Supplier.ACCOUNT_TYPE_CHOICES,
        blank=True,
    )
    supplier_account_number = models.CharField("Número de cuenta (compra)", max_length=60, blank=True)
    supplier_bank_name = models.CharField("Banco (compra)", max_length=120, blank=True)
    approved_at = models.DateTimeField("Aprobado en", blank=True, null=True)
    scope_batch_code = models.CharField("Lote asociado", max_length=60, blank=True)
    support_document_type = models.ForeignKey(
        SupportDocumentType,
        on_delete=models.PROTECT,
        related_name="purchase_requests",
        verbose_name="Tipo de soporte",
        null=True,
        blank=True,
    )
    support_template_values = models.JSONField(
        "Valores personalizados del soporte",
        default=dict,
        blank=True,
        help_text="Valores usados para completar la plantilla interna.",
    )
    reception_mismatch = models.BooleanField(
        "Recepción con diferencias",
        default=False,
    )
    accounted_in_system = models.BooleanField(
        "Contabilizado en sistema",
        default=False,
        help_text="Indica si la compra ya fue registrada en el sistema contable.",
    )
    accounted_at = models.DateTimeField(
        "Fecha de contabilización",
        blank=True,
        null=True,
    )

    class Meta:
        verbose_name = "Solicitud de compra"
        verbose_name_plural = "Solicitudes de compra"
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.timeline_code} - {self.name}"

    def stage_status(self, stage_code: str) -> str:
        flow = [status for _, status in self.STAGE_FLOW]
        stage_index = {code: index for index, (code, _) in enumerate(self.STAGE_FLOW)}
        target_index = stage_index.get(stage_code)
        if target_index is None:
            return "locked"
        current_index = flow.index(self.status)
        if current_index > target_index:
            return "completed"
        if current_index == target_index:
            return "active"
        if current_index + 1 == target_index:
            return "pending"
        return "locked"

    @property
    def scope_label(self) -> str:
        base = self.expense_type.name
        location_bits = self._area_location_bits()
        batch_label = self._scope_batch_label()
        if batch_label:
            location_bits.append(batch_label)
        if not location_bits:
            return base
        return f"{base} · {' / '.join(location_bits)}"

    def _scope_batch_label(self) -> str:
        code = (self.scope_batch_code or '').strip()
        if not code:
            return ''
        normalized = code.lower()
        if normalized.startswith('lote'):
            return code
        return f"Lote {code}"

    @property
    def primary_scope_area(self) -> str:
        summary = self._primary_area_summary()
        if summary:
            return summary['kind']
        return self.AreaScope.COMPANY

    @property
    def primary_scope_farm(self) -> Farm | None:
        summary = self._primary_area_summary()
        return summary.get('farm') if summary else None

    @property
    def primary_scope_chicken_house(self) -> ChickenHouse | None:
        summary = self._primary_area_summary()
        return summary.get('chicken_house') if summary else None

    def get_scope_area_display(self) -> str:
        return self.AreaScope(self.primary_scope_area).label

    def _primary_area_summary(self) -> dict | None:
        summaries = self._area_summaries()
        if summaries:
            return summaries[0]
        return None

    def _area_location_bits(self) -> list[str]:
        summaries = self._area_summaries()
        labels: list[str] = []
        for summary in summaries:
            label = summary.get('label')
            if label:
                labels.append(label)
            if len(labels) == 2:
                break
        return labels

    def _area_summaries(self) -> list[dict]:
        cached = getattr(self, '_cached_area_summaries', None)
        if cached is not None:
            return cached
        seen: OrderedDict[tuple, dict] = OrderedDict()
        for item in self._iter_scope_items():
            kind = item.scope_area or self.AreaScope.COMPANY
            farm = item.scope_farm
            house = item.scope_chicken_house
            key = (kind, getattr(farm, 'id', None), getattr(house, 'id', None))
            if key in seen:
                continue
            seen[key] = {
                'kind': kind,
                'farm': farm,
                'chicken_house': house,
                'label': item.area_label,
            }
        summaries = list(seen.values())
        self._cached_area_summaries = summaries
        return summaries

    def _iter_scope_items(self) -> list['PurchaseItem']:
        if hasattr(self, '_cached_scope_items'):
            return self._cached_scope_items
        items_qs = self.items.all()
        prefetched = getattr(self, '_prefetched_objects_cache', None)
        if not prefetched or 'items' not in prefetched:
            items_qs = items_qs.select_related('scope_farm', 'scope_chicken_house__farm')
        items = list(items_qs)
        self._cached_scope_items = items
        return items

    @property
    def area_label(self) -> str:
        summaries = self._area_summaries()
        if not summaries:
            return self.AreaScope.COMPANY.label
        labels = [summary['label'] for summary in summaries if summary['label']]
        if not labels:
            return self.AreaScope.COMPANY.label
        if len(labels) == 1:
            return labels[0]
        if len(labels) == 2:
            return " / ".join(labels)
        remaining = len(labels) - 2
        return f"{labels[0]} / {labels[1]} + {remaining} más"

    @property
    def latest_approval_note(self) -> str:
        approval = (
            self.approvals.filter(status=PurchaseApproval.Status.APPROVED)
            .order_by('-decided_at', '-updated_at')
            .first()
        )
        if approval and approval.comments:
            return approval.comments
        return ''

    @property
    def has_reception_anomalies(self) -> bool:
        return self.reception_mismatch

    @property
    def show_payment_breakdown(self) -> bool:
        return self.status in self.POST_PAYMENT_STATUSES


class PurchaseItem(TimeStampedModel):
    purchase = models.ForeignKey(
        PurchaseRequest,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name="Solicitud",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.SET_NULL,
        related_name="purchase_items",
        verbose_name="Producto",
        blank=True,
        null=True,
    )
    description = models.CharField("Descripción", max_length=255)
    quantity = models.DecimalField(
        "Cantidad",
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )
    estimated_amount = models.DecimalField(
        "Monto estimado",
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    received_quantity = models.DecimalField(
        "Cantidad recibida",
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(0)],
    )
    scope_area = models.CharField(
        "Área",
        max_length=20,
        choices=PurchaseRequest.AreaScope.choices,
        default=PurchaseRequest.AreaScope.COMPANY,
    )
    scope_farm = models.ForeignKey(
        Farm,
        on_delete=models.SET_NULL,
        related_name="purchase_items",
        verbose_name="Granja",
        blank=True,
        null=True,
    )
    scope_chicken_house = models.ForeignKey(
        ChickenHouse,
        on_delete=models.SET_NULL,
        related_name="purchase_items",
        verbose_name="Galpón",
        blank=True,
        null=True,
    )

    class Meta:
        verbose_name = "Item de compra"
        verbose_name_plural = "Items de compra"

    def __str__(self) -> str:
        return self.description

    @property
    def area_label(self) -> str:
        if self.scope_area == PurchaseRequest.AreaScope.CHICKEN_HOUSE:
            if self.scope_chicken_house:
                farm_name = self.scope_chicken_house.farm.name if self.scope_chicken_house.farm else ''
                if farm_name:
                    return f"{farm_name} · {self.scope_chicken_house.name}"
                return self.scope_chicken_house.name
            return PurchaseRequest.AreaScope.CHICKEN_HOUSE.label
        if self.scope_area == PurchaseRequest.AreaScope.FARM:
            if self.scope_farm:
                return self.scope_farm.name
            return PurchaseRequest.AreaScope.FARM.label
        return PurchaseRequest.AreaScope.COMPANY.label

    def scope_value(self) -> str:
        if self.scope_area == PurchaseRequest.AreaScope.CHICKEN_HOUSE and self.scope_chicken_house_id:
            return f"{PurchaseRequest.AreaScope.CHICKEN_HOUSE}:{self.scope_chicken_house_id}"
        if self.scope_area == PurchaseRequest.AreaScope.FARM and self.scope_farm_id:
            return f"{PurchaseRequest.AreaScope.FARM}:{self.scope_farm_id}"
        return PurchaseRequest.AreaScope.COMPANY


class PurchaseReceptionAttachment(TimeStampedModel):
    purchase = models.ForeignKey(
        PurchaseRequest,
        on_delete=models.CASCADE,
        related_name="reception_attachments",
        verbose_name="Solicitud",
    )
    file = models.FileField("Archivo", upload_to="purchases/receptions/%Y/%m/")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="purchase_reception_attachments",
        verbose_name="Subido por",
    )
    notes = models.CharField("Notas", max_length=255, blank=True)

    class Meta:
        verbose_name = "Adjunto de recepción"
        verbose_name_plural = "Adjuntos de recepción"
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return self.filename

    @property
    def filename(self) -> str:
        return os.path.basename(self.file.name)


class PurchaseSupportAttachment(TimeStampedModel):
    purchase = models.ForeignKey(
        PurchaseRequest,
        on_delete=models.CASCADE,
        related_name="support_attachments",
        verbose_name="Solicitud",
    )
    file = models.FileField("Archivo", upload_to="purchases/support/%Y/%m/")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="purchase_support_attachments",
        verbose_name="Subido por",
    )
    notes = models.CharField("Notas", max_length=255, blank=True)

    class Meta:
        verbose_name = "Adjunto de soporte"
        verbose_name_plural = "Adjuntos de soporte"
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return self.filename

    @property
    def filename(self) -> str:
        return os.path.basename(self.file.name)


class PurchaseApproval(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pendiente"
        APPROVED = "approved", "Aprobado"
        REJECTED = "rejected", "Rechazado"

    purchase_request = models.ForeignKey(
        PurchaseRequest,
        on_delete=models.CASCADE,
        related_name="approvals",
        verbose_name="Solicitud",
    )
    rule = models.ForeignKey(
        ExpenseTypeApprovalRule,
        on_delete=models.SET_NULL,
        related_name="purchase_approvals",
        blank=True,
        null=True,
        verbose_name="Regla",
    )
    sequence = models.PositiveSmallIntegerField("Secuencia")
    role = models.CharField("Rol", max_length=150)
    approver = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="purchase_approvals",
        blank=True,
        null=True,
        verbose_name="Aprobador",
    )
    status = models.CharField(
        "Estado",
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
    )
    comments = models.TextField("Comentarios", blank=True)
    decided_at = models.DateTimeField("Decidido en", blank=True, null=True)

    class Meta:
        verbose_name = "Aprobación de compra"
        verbose_name_plural = "Aprobaciones de compra"
        ordering = ("purchase_request", "sequence")
        constraints = [
            models.UniqueConstraint(
                fields=("purchase_request", "sequence"),
                name="unique_purchase_request_sequence",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.purchase_request.timeline_code} · Paso {self.sequence}"


class PurchaseAuditLog(TimeStampedModel):
    purchase_request = models.ForeignKey(
        PurchaseRequest,
        on_delete=models.CASCADE,
        related_name="audit_logs",
        verbose_name="Solicitud",
    )
    event = models.CharField("Evento", max_length=120)
    message = models.TextField("Mensaje", blank=True)
    payload = models.JSONField("Detalle", default=dict, blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="purchase_audit_logs",
        blank=True,
        null=True,
        verbose_name="Actor",
    )

    class Meta:
        verbose_name = "Log de auditoría"
        verbose_name_plural = "Logs de auditoría"
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.purchase_request.timeline_code} · {self.event}"


class PayrollSnapshot(TimeStampedModel):
    class LastAction(models.TextChoices):
        GENERATE = "generate", "Generación inicial"
        UPDATE = "update", "Actualización"
        APPLY = "apply", "Ajuste manual"
        EXPORT = "export", "Exportación"

    start_date = models.DateField("Fecha inicial")
    end_date = models.DateField("Fecha final")
    payload = models.JSONField("Resumen almacenado", default=dict, blank=True)
    last_computed_at = models.DateTimeField("Calculado en", null=True, blank=True)
    last_computed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="payroll_snapshots",
        null=True,
        blank=True,
    )
    last_action = models.CharField(
        "Última acción",
        max_length=20,
        choices=LastAction.choices,
        default=LastAction.GENERATE,
    )

    class Meta:
        verbose_name = "Nómina almacenada"
        verbose_name_plural = "Nóminas almacenadas"
        ordering = ("-start_date", "-end_date")
        constraints = [
            models.UniqueConstraint(
                fields=("start_date", "end_date"),
                name="unique_payroll_period_snapshot",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.start_date:%Y-%m-%d} / {self.end_date:%Y-%m-%d}"


class Sale(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Pre-factura"
        CONFIRMED = "confirmed", "Venta confirmada"
        PAID = "paid", "Venta pagada"

    class PaymentCondition(models.TextChoices):
        CASH = "cash", "Contado"
        CREDIT = "credit", "Crédito"

    date = models.DateField("Fecha de venta")
    customer = models.ForeignKey(
        Supplier,
        on_delete=models.PROTECT,
        related_name="sales",
        verbose_name="Cliente (tercero)",
    )
    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="sales",
        verbose_name="Vendedor",
    )
    warehouse_destination = models.CharField(
        "Bodega (destino)",
        max_length=32,
        choices=EggDispatchDestination.choices,
        blank=True,
    )
    status = models.CharField(
        "Estado",
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    payment_condition = models.CharField(
        "Condición de pago",
        max_length=20,
        choices=PaymentCondition.choices,
        default=PaymentCondition.CREDIT,
    )
    payment_due_date = models.DateField("Fecha esperada de pago", null=True, blank=True)
    total_amount = models.DecimalField(
        "Total factura",
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    auto_withholding_amount = models.DecimalField(
        "Autorretención",
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    notes = models.TextField("Notas para el equipo comercial", blank=True)
    confirmed_at = models.DateTimeField("Confirmado en", null=True, blank=True)
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="sales_confirmed",
        null=True,
        blank=True,
    )
    paid_at = models.DateTimeField("Pagado en", null=True, blank=True)

    class Meta:
        verbose_name = "Venta"
        verbose_name_plural = "Ventas"
        ordering = ("-date", "-id")

    def __str__(self) -> str:
        return f"Venta #{self.pk}"

    @property
    def inventory_destination_label(self) -> str:
        if not self.warehouse_destination:
            return ""
        return dict(EggDispatchDestination.choices).get(self.warehouse_destination, self.warehouse_destination)

    @property
    def subtotal_amount(self) -> Decimal:
        items_manager = getattr(self, "items", None)
        if items_manager is None:
            return Decimal("0.00")
        return sum((Decimal(item.subtotal or 0) for item in items_manager.all()), Decimal("0.00"))

    @property
    def payments_total(self) -> Decimal:
        payments_manager = getattr(self, "payments", None)
        if payments_manager is None:
            return Decimal("0.00")
        return sum((Decimal(payment.amount or 0) for payment in payments_manager.all()), Decimal("0.00"))

    @property
    def balance_due(self) -> Decimal:
        subtotal = Decimal(self.total_amount or Decimal("0.00"))
        withholding = Decimal(self.auto_withholding_amount or Decimal("0.00"))
        paid = Decimal(self.payments_total)
        balance = subtotal - withholding - paid
        if balance < Decimal("0.00"):
            return Decimal("0.00")
        return balance


class SaleProductType(models.TextChoices):
    JUMBO = "jumbo", "Jumbo"
    TRIPLE_A = "aaa", "AAA"
    DOUBLE_A = "aa", "AA"
    SINGLE_A = "a", "A"
    B = "b", "B"
    C = "c", "C"
    D = "d", "D"
    HEN = "hen", "Gallina"
    HEN_MANURE = "hen_manure", "Gallinaza"


class SaleItem(TimeStampedModel):
    sale = models.ForeignKey(
        Sale,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name="Venta",
    )
    product_type = models.CharField(
        "Producto",
        max_length=16,
        choices=SaleProductType.choices,
    )
    quantity = models.DecimalField(
        "Cantidad",
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )
    unit_price = models.DecimalField(
        "Precio unitario",
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )
    subtotal = models.DecimalField(
        "Subtotal",
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )

    class Meta:
        verbose_name = "Detalle de venta"
        verbose_name_plural = "Detalles de venta"
        ordering = ("product_type",)

    def __str__(self) -> str:
        label = dict(SaleProductType.choices).get(self.product_type, self.product_type)
        return f"{label} · {self.quantity}"

    def compute_subtotal(self) -> Decimal:
        return (Decimal(self.quantity or 0) * Decimal(self.unit_price or 0)).quantize(Decimal("0.01"))

    def save(self, *args, **kwargs):
        self.subtotal = self.compute_subtotal()
        super().save(*args, **kwargs)


class SalePayment(TimeStampedModel):
    class Method(models.TextChoices):
        CASH = "cash", "Efectivo"
        TRANSFER = "transfer", "Transferencia"

    sale = models.ForeignKey(
        Sale,
        on_delete=models.CASCADE,
        related_name="payments",
        verbose_name="Venta",
    )
    date = models.DateField("Fecha de abono")
    amount = models.DecimalField(
        "Monto",
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )
    method = models.CharField(
        "Modalidad",
        max_length=20,
        choices=Method.choices,
        default=Method.CASH,
    )
    notes = models.CharField("Notas del abono", max_length=255, blank=True)

    class Meta:
        verbose_name = "Abono de venta"
        verbose_name_plural = "Abonos de venta"
        ordering = ("-date", "-id")

    def __str__(self) -> str:
        return f"{self.get_method_display()} · {self.amount}"

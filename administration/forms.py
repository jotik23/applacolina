from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any, Dict, List

from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.forms import BaseInlineFormSet, inlineformset_factory
from django.utils import timezone

from production.models import EggDispatchDestination

from .services.payroll import PayrollComputationError, PayrollPeriodInfo, resolve_payroll_period
from .services.sales import (
    SALE_EGG_TYPE_MAP,
    SALE_PRODUCT_ORDER,
    get_inventory_for_seller_destination,
    refresh_sale_payment_state,
)
from .models import (
    ExpenseTypeApprovalRule,
    Product,
    PurchasingExpenseType,
    Sale,
    SaleItem,
    SalePayment,
    SaleProductType,
    Supplier,
    SupportDocumentType,
)


class SupplierForm(forms.ModelForm):
    class Meta:
        model = Supplier
        fields = [
            "name",
            "tax_id",
            "contact_name",
            "contact_email",
            "contact_phone",
            "address",
            "city",
            "account_holder_id",
            "account_holder_name",
            "account_type",
            "account_number",
            "bank_name",
        ]


class SupplierImportForm(forms.Form):
    file = forms.FileField(
        label="Archivo de Excel (.xlsx)",
        help_text="Debe incluir columnas para Nombre y Número de identificación.",
        widget=forms.FileInput(attrs={"accept": ".xlsx"}),
    )

    def clean_file(self):
        uploaded = self.cleaned_data["file"]
        filename = uploaded.name.lower()
        if not filename.endswith(".xlsx"):
            raise ValidationError("El archivo debe tener extensión .xlsx.")
        return uploaded


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ["name", "unit"]


class PurchasingExpenseTypeForm(forms.ModelForm):
    class Meta:
        model = PurchasingExpenseType
        fields = [
            "name",
            "parent_category",
            "default_support_document_type",
            "iva_rate",
            "withholding_rate",
            "assumed_withholding_rate",
        ]

    def clean_parent_category(self):
        parent = self.cleaned_data.get("parent_category")
        instance = self.instance
        if parent and instance.pk and parent.pk == instance.pk:
            raise ValidationError("La categoría padre no puede ser la misma categoría.")
        return parent


class SupportDocumentTypeForm(forms.ModelForm):
    class Meta:
        model = SupportDocumentType
        fields = ["name", "kind", "template"]

    def clean(self):
        cleaned = super().clean()
        kind = cleaned.get("kind")
        template = (cleaned.get("template") or "").strip()
        cleaned["template"] = template
        if kind == SupportDocumentType.Kind.INTERNAL and not template:
            self.add_error("template", "Ingresa el HTML que se usará para el soporte interno.")
        return cleaned


class ExpenseTypeApprovalRuleForm(forms.ModelForm):
    class Meta:
        model = ExpenseTypeApprovalRule
        fields = ["approver"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        approver_field = self.fields.get("approver")
        if approver_field is not None:
            user_model = get_user_model()
            approver_field.queryset = user_model.objects.filter(Q(is_staff=True) | Q(is_superuser=True))


class BaseExpenseTypeWorkflowFormSet(BaseInlineFormSet):
    pass


ExpenseTypeWorkflowFormSet = inlineformset_factory(
    PurchasingExpenseType,
    ExpenseTypeApprovalRule,
    form=ExpenseTypeApprovalRuleForm,
    formset=BaseExpenseTypeWorkflowFormSet,
    extra=0,
    can_delete=True,
)


class PayrollPeriodForm(forms.Form):
    start_date = forms.DateField(
        label="Fecha inicial",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    end_date = forms.DateField(
        label="Fecha final",
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    period_info: PayrollPeriodInfo | None = None

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("start_date")
        end = cleaned.get("end_date")
        if start and end:
            try:
                self.period_info = resolve_payroll_period(start, end)
            except PayrollComputationError as exc:
                raise ValidationError(str(exc))
        return cleaned


class SaleForm(forms.ModelForm):
    input_classes = (
        "block w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm "
        "font-semibold text-slate-700 shadow-inner transition focus:border-emerald-400 "
        "focus:outline-none focus:ring-2 focus:ring-emerald-100"
    )
    product_order = SALE_PRODUCT_ORDER

    class Meta:
        model = Sale
        fields = [
            "date",
            "customer",
            "seller",
            "status",
            "warehouse_destination",
            "payment_condition",
            "payment_due_date",
            "discount_amount",
            "notes",
        ]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "payment_due_date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args: Any, actor_id: int | None = None, **kwargs: Any) -> None:
        self.actor_id = actor_id
        super().__init__(*args, **kwargs)
        self.cleaned_items: Dict[str, Dict[str, Decimal]] = {}
        self.cleaned_total = Decimal("0.00")
        self.inventory_snapshot: Dict[str, Decimal] = {}
        self.quantity_field_map: Dict[str, str] = {}
        self.price_field_map: Dict[str, str] = {}
        self.cleaned_discount = Decimal("0.00")
        self.net_total = Decimal("0.00")
        self.cleaned_withholding = Decimal("0.00")
        self._configure_base_fields()
        self._build_product_fields()

    def _configure_base_fields(self) -> None:
        supplier_field = self.fields["customer"]
        supplier_field.queryset = Supplier.objects.order_by("name")
        supplier_field.widget.attrs.setdefault("class", self.input_classes)
        seller_field = self.fields["seller"]
        user_model = get_user_model()
        seller_field.queryset = user_model.objects.order_by("apellidos", "nombres", "cedula")
        seller_field.widget.attrs.setdefault("class", self.input_classes)

        date_field = self.fields["date"]
        date_field.widget.attrs.setdefault("class", self.input_classes)
        if (
            not self.is_bound
            and not date_field.initial
            and not getattr(self.instance, "pk", None)
        ):
            today = timezone.localdate()
            date_field.initial = today
            date_field.widget.attrs.setdefault("value", today.isoformat())

        status_field = self.fields["status"]
        status_field.widget.attrs.setdefault("class", self.input_classes)
        status_field.choices = [
            (Sale.Status.DRAFT, Sale.Status.DRAFT.label),
            (Sale.Status.CONFIRMED, Sale.Status.CONFIRMED.label),
        ]
        if self.instance.pk and self.instance.status == Sale.Status.PAID:
            status_field.choices.append((Sale.Status.PAID, Sale.Status.PAID.label))
            status_field.disabled = True

        warehouse_field = self.fields["warehouse_destination"]
        warehouse_field.widget.attrs.setdefault("class", self.input_classes)
        warehouse_field.required = False

        payment_condition_field = self.fields["payment_condition"]
        payment_condition_field.widget.attrs.setdefault("class", self.input_classes)
        payment_due_field = self.fields["payment_due_date"]
        payment_due_field.widget.attrs.setdefault("class", self.input_classes)
        if not payment_due_field.initial and not getattr(self.instance, "pk", None):
            payment_due_field.initial = timezone.localdate() + timedelta(days=3)

        discount_field = self.fields["discount_amount"]
        discount_field.widget.attrs.setdefault("class", f"{self.input_classes} text-right")
        discount_field.widget.attrs.setdefault("step", "0.01")
        discount_field.widget.attrs.setdefault("min", "0")
        discount_field.required = False

        notes_field = self.fields["notes"]
        existing = notes_field.widget.attrs.get("class", "")
        notes_field.widget.attrs["class"] = f"{existing} {self.input_classes}".strip()
        notes_field.widget.attrs.setdefault(
            "placeholder", "Centraliza instrucciones o compromisos con el cliente."
        )

    def _build_product_fields(self) -> None:
        existing_items: Dict[str, SaleItem] = {}
        if self.instance and self.instance.pk:
            existing_items = {
                item.product_type: item for item in self.instance.items.all()  # type: ignore[attr-defined]
            }
        label_map = dict(SaleProductType.choices)
        for product_type in self.product_order:
            quantity_field_name = self._quantity_field(product_type)
            price_field_name = self._price_field(product_type)
            quantity_field = forms.DecimalField(
                required=False,
                min_value=Decimal("0"),
                decimal_places=2,
                max_digits=10,
                label=f"Cantidad {label_map.get(product_type, product_type)}",
                widget=forms.NumberInput(
                    attrs={
                        "class": f"{self.input_classes} text-right",
                        "step": "0.01",
                        "min": "0",
                        "placeholder": "0.00",
                    }
                ),
            )
            price_field = forms.DecimalField(
                required=False,
                min_value=Decimal("0"),
                decimal_places=2,
                max_digits=12,
                label=f"Precio {label_map.get(product_type, product_type)}",
                widget=forms.NumberInput(
                    attrs={
                        "class": f"{self.input_classes} text-right",
                        "step": "0.01",
                        "min": "0",
                        "placeholder": "0.00",
                    }
                ),
            )
            existing_item = existing_items.get(product_type)
            if existing_item:
                quantity_field.initial = existing_item.quantity
                price_field.initial = existing_item.unit_price
            self.fields[quantity_field_name] = quantity_field
            self.fields[price_field_name] = price_field
            self.quantity_field_map[product_type] = quantity_field_name
            self.price_field_map[product_type] = price_field_name

    def _quantity_field(self, product_type: str) -> str:
        return f"quantity_{product_type}"

    def _price_field(self, product_type: str) -> str:
        return f"unit_price_{product_type}"

    @property
    def product_rows(self) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        label_map = dict(SaleProductType.choices)
        for product_type in self.product_order:
            rows.append(
                {
                    "code": product_type,
                    "label": label_map.get(product_type, product_type),
                    "quantity_field": self[self.quantity_field_map[product_type]],
                    "price_field": self[self.price_field_map[product_type]],
                }
            )
        return rows

    def clean(self) -> Dict[str, Any]:
        cleaned = super().clean()
        self._validate_items(cleaned)
        self._validate_payment_rules(cleaned)
        self._validate_inventory(cleaned)
        self._compute_financial_summary(cleaned)
        return cleaned

    def _validate_items(self, cleaned: Dict[str, Any]) -> None:
        entries: Dict[str, Dict[str, Decimal]] = {}
        total = Decimal("0.00")
        for product_type in self.product_order:
            quantity_value = cleaned.get(self.quantity_field_map[product_type])
            price_value = cleaned.get(self.price_field_map[product_type])
            if quantity_value in (None, "") and price_value in (None, ""):
                continue
            quantity = Decimal(quantity_value or 0)
            price = Decimal(price_value or 0)
            if quantity <= Decimal("0"):
                self.add_error(self.quantity_field_map[product_type], "Ingresa una cantidad mayor a cero.")
                continue
            if price <= Decimal("0"):
                self.add_error(self.price_field_map[product_type], "Configura el precio unitario para este producto.")
                continue
            subtotal = (quantity * price).quantize(Decimal("0.01"))
            entries[product_type] = {
                "quantity": quantity,
                "unit_price": price,
                "subtotal": subtotal,
            }
            total += subtotal
        if not entries:
            raise ValidationError("Registra al menos un producto con cantidad y precio.")
        self.cleaned_items = entries
        self.cleaned_total = total

    def _validate_payment_rules(self, cleaned: Dict[str, Any]) -> None:
        condition = cleaned.get("payment_condition")
        due_date = cleaned.get("payment_due_date")
        if condition == Sale.PaymentCondition.CREDIT:
            if not due_date:
                self.add_error("payment_due_date", "Define la fecha estimada en la que el cliente pagará este crédito.")
        else:
            cleaned["payment_due_date"] = None

    def _validate_inventory(self, cleaned: Dict[str, Any]) -> None:
        status = cleaned.get("status") or Sale.Status.DRAFT
        if status == Sale.Status.DRAFT:
            return
        seller = cleaned.get("seller")
        destination = cleaned.get("warehouse_destination")
        if not destination:
            self.add_error(
                "warehouse_destination",
                "Selecciona la bodega desde donde se descontará el inventario del vendedor.",
            )
            return
        if not seller:
            self.add_error("seller", "Selecciona un vendedor para validar inventario.")
            return

        inventory = get_inventory_for_seller_destination(
            seller_id=getattr(seller, "pk", None),
            destination=destination,
            exclude_sale_id=getattr(self.instance, "pk", None),
        )
        self.inventory_snapshot = inventory
        destination_label = dict(EggDispatchDestination.choices).get(destination, destination)

        for product_type, payload in self.cleaned_items.items():
            egg_type = SALE_EGG_TYPE_MAP.get(product_type)
            if not egg_type:
                continue
            available = inventory.get(egg_type, Decimal("0"))
            if payload["quantity"] > available:
                field_name = self.quantity_field_map[product_type]
                product_label = dict(SaleProductType.choices).get(product_type, product_type)
                self.add_error(
                    field_name,
                    f"Inventario insuficiente en la bodega {destination_label}. "
                    f"Disponible {available} cartones para {product_label}.",
                )

    def _compute_financial_summary(self, cleaned: Dict[str, Any]) -> None:
        discount = Decimal(cleaned.get("discount_amount") or 0)
        if discount < Decimal("0"):
            self.add_error("discount_amount", "El descuento no puede ser negativo.")
            discount = Decimal("0")
        if discount > self.cleaned_total:
            self.add_error("discount_amount", "El descuento no puede superar el subtotal de la factura.")
            discount = Decimal("0")
        self.cleaned_discount = discount.quantize(Decimal("0.01"))
        net_total = self.cleaned_total - self.cleaned_discount
        if net_total < Decimal("0"):
            net_total = Decimal("0")
        self.net_total = net_total.quantize(Decimal("0.01"))
        self.cleaned_withholding = (self.net_total * Decimal("0.01")).quantize(Decimal("0.01"))

    def get_inventory_preview(self) -> Dict[str, Decimal]:
        if self.inventory_snapshot:
            return self.inventory_snapshot
        seller_id, destination = self._current_seller_destination()
        return get_inventory_for_seller_destination(
            seller_id=seller_id,
            destination=destination,
            exclude_sale_id=getattr(self.instance, "pk", None),
        )

    def get_product_inventory_payload(self) -> Dict[str, Decimal]:
        preview = self.get_inventory_preview()
        payload: Dict[str, Decimal] = {}
        for product_code, egg_type in SALE_EGG_TYPE_MAP.items():
            available = preview.get(egg_type)
            if available is None:
                continue
            payload[product_code] = available
        return payload

    def get_financial_snapshot(self) -> Dict[str, Decimal]:
        if self.is_bound:
            subtotal = self.cleaned_total
            discount = self.cleaned_discount
            net_total = self.net_total
            withholding = self.cleaned_withholding
        elif self.instance and self.instance.pk:
            subtotal = self.instance.subtotal_amount
            discount = Decimal(self.instance.discount_amount or 0)
            net_total = self.instance.total_amount
            withholding = self.instance.auto_withholding_amount
        else:
            subtotal = self.cleaned_total
            discount = self.cleaned_discount
            net_total = self.net_total
            withholding = self.cleaned_withholding
        if net_total < Decimal("0"):
            net_total = Decimal("0.00")
        return {
            "subtotal": subtotal,
            "discount": discount,
            "total": net_total,
            "withholding": withholding,
        }

    def _current_seller_destination(self) -> tuple[int | None, str | None]:
        seller_field_name = self.add_prefix("seller")
        destination_field_name = self.add_prefix("warehouse_destination")
        seller_id: int | None = None
        destination: str | None = None
        seller_value = self.data.get(seller_field_name) if self.is_bound else None
        destination_value = self.data.get(destination_field_name) if self.is_bound else None
        if seller_value:
            try:
                seller_id = int(seller_value)
            except (TypeError, ValueError):
                seller_id = None
        elif self.instance and self.instance.pk:
            seller_id = self.instance.seller_id
        elif self.initial.get("seller"):
            initial_seller = self.initial["seller"]
            seller_id = getattr(initial_seller, "pk", initial_seller)

        if destination_value:
            destination = destination_value
        elif self.instance and self.instance.pk:
            destination = self.instance.warehouse_destination
        elif self.initial.get("warehouse_destination"):
            destination = self.initial["warehouse_destination"]
        return seller_id, destination

    def save(self, commit: bool = True) -> Sale:
        if not self.cleaned_items:
            raise ValueError("La venta carece de items limpios al guardar.")
        sale: Sale = super().save(commit=False)
        if sale.status == Sale.Status.DRAFT:
            sale.warehouse_destination = ""
            sale.confirmed_at = None
            sale.confirmed_by = None
        elif sale.status == Sale.Status.CONFIRMED and not sale.confirmed_at:
            sale.confirmed_at = timezone.now()
            if self.actor_id and not sale.confirmed_by_id:
                sale.confirmed_by_id = self.actor_id
        sale.discount_amount = self.cleaned_discount
        if commit:
            sale.save()
            sale.items.all().delete()
            SaleItem.objects.bulk_create(
                [
                    SaleItem(
                        sale=sale,
                        product_type=product_type,
                        quantity=payload["quantity"],
                        unit_price=payload["unit_price"],
                        subtotal=payload["subtotal"],
                    )
                    for product_type, payload in self.cleaned_items.items()
                ]
            )
            refresh_sale_payment_state(sale)
        return sale


class SalePaymentForm(forms.ModelForm):
    input_classes = SaleForm.input_classes

    class Meta:
        model = SalePayment
        fields = ["date", "amount", "method", "notes"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args: Any, sale: Sale, **kwargs: Any) -> None:
        self.sale = sale
        super().__init__(*args, **kwargs)
        date_field = self.fields["date"]
        if not self.is_bound and not date_field.initial:
            today = timezone.localdate()
            date_field.initial = today
            date_field.widget.attrs.setdefault("value", today.isoformat())
        for field_name in ["date", "amount", "method"]:
            self.fields[field_name].widget.attrs.setdefault("class", self.input_classes)
        amount_field = self.fields["amount"]
        amount_field.widget.attrs.setdefault("class", f"{self.input_classes} text-right")
        amount_field.widget.attrs.setdefault("step", "0.01")
        amount_field.widget.attrs.setdefault("min", "0")
        notes_field = self.fields["notes"]
        existing = notes_field.widget.attrs.get("class", "")
        notes_field.widget.attrs["class"] = f"{existing} {self.input_classes}".strip()

    def clean_amount(self) -> Decimal:
        amount = self.cleaned_data.get("amount") or Decimal("0")
        if amount <= Decimal("0"):
            raise ValidationError("El abono debe ser mayor a cero.")
        balance = self.sale.balance_due
        if amount > balance:
            raise ValidationError(
                f"El abono supera el saldo pendiente ({balance}). Actualiza la venta si el valor a pagar cambió."
            )
        return amount

from __future__ import annotations

from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.forms import BaseInlineFormSet, inlineformset_factory

from .services.payroll import PayrollComputationError, PayrollPeriodInfo, resolve_payroll_period
from .models import (
    ExpenseTypeApprovalRule,
    Product,
    PurchasingExpenseType,
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

from __future__ import annotations

from django import forms
from django.forms import BaseInlineFormSet, inlineformset_factory
from django.core.exceptions import ValidationError

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

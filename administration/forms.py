from __future__ import annotations

from django import forms
from django.forms import BaseInlineFormSet, inlineformset_factory
from django.core.exceptions import ValidationError

from .models import (
    ExpenseTypeApprovalRule,
    PurchasingExpenseType,
    Supplier,
)


class SupplierForm(forms.ModelForm):
    class Meta:
        model = Supplier
        fields = [
            "name",
            "tax_id",
            "tax_regime",
            "payment_terms_days",
            "is_active",
            "contact_name",
            "contact_email",
            "contact_phone",
            "address",
            "city",
            "bank_name",
            "bank_account_type",
            "bank_account_number",
            "requires_vat_retention",
            "requires_ica_retention",
            "requires_rtefte",
            "notes",
        ]

    def clean_payment_terms_days(self):
        value = self.cleaned_data["payment_terms_days"]
        if value <= 0:
            raise ValidationError("El plazo debe ser mayor que cero.")
        return value


class PurchasingExpenseTypeForm(forms.ModelForm):
    class Meta:
        model = PurchasingExpenseType
        fields = [
            "name",
            "scope",
            "iva_rate",
            "withholding_rate",
            "self_withholding_rate",
            "parent_category",
            "is_active",
        ]

    def clean_parent_category(self):
        parent = self.cleaned_data.get("parent_category")
        instance = self.instance
        if parent and instance.pk and parent.pk == instance.pk:
            raise ValidationError("La categoría padre no puede ser la misma categoría.")
        return parent


class ExpenseTypeApprovalRuleForm(forms.ModelForm):
    sequence = forms.IntegerField(min_value=1, label="Secuencia")

    class Meta:
        model = ExpenseTypeApprovalRule
        fields = ["sequence", "name", "approver"]


class BaseExpenseTypeWorkflowFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        sequences: set[int] = set()
        for form in self.forms:
            if not hasattr(form, "cleaned_data"):
                continue
            if form.cleaned_data.get("DELETE"):
                continue
            sequence = form.cleaned_data.get("sequence")
            if sequence is None:
                continue
            if sequence in sequences:
                form.add_error("sequence", "La secuencia debe ser única dentro del flujo.")
            sequences.add(sequence)


ExpenseTypeWorkflowFormSet = inlineformset_factory(
    PurchasingExpenseType,
    ExpenseTypeApprovalRule,
    form=ExpenseTypeApprovalRuleForm,
    formset=BaseExpenseTypeWorkflowFormSet,
    extra=0,
    can_delete=True,
)

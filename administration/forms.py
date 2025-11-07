from __future__ import annotations

from django import forms
from django.core.exceptions import ValidationError

from production.models import ChickenHouse

from .models import (
    CostCenterConfig,
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
            "code",
            "name",
            "scope",
            "description",
            "iva_rate",
            "withholding_rate",
            "requires_invoice",
            "requires_supporting_docs",
            "mandatory_documents",
            "is_active",
        ]


class ExpenseTypeApprovalRuleForm(forms.ModelForm):
    class Meta:
        model = ExpenseTypeApprovalRule
        fields = ["sequence", "name", "approver"]


class CostCenterConfigForm(forms.ModelForm):
    class Meta:
        model = CostCenterConfig
        fields = [
            "expense_type",
            "name",
            "scope",
            "allocation_method",
            "percentage",
            "valid_from",
            "valid_until",
            "is_required",
            "farm",
            "chicken_house",
            "notes",
            "is_active",
        ]

    def clean_percentage(self):
        value = self.cleaned_data["percentage"]
        if value <= 0:
            raise ValidationError("El porcentaje debe ser mayor que 0%.")
        return value

    def clean(self):
        cleaned = super().clean()
        farm = cleaned.get("farm")
        chicken_house: ChickenHouse | None = cleaned.get("chicken_house")
        if chicken_house and farm and chicken_house.farm_id != farm.id:
            self.add_error("chicken_house", "El galpón debe pertenecer a la granja seleccionada.")
        if chicken_house and not farm:
            self.add_error("farm", "Selecciona la granja antes de elegir un galpón.")
        return cleaned

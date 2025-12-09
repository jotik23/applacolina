from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model
from django.utils import timezone

from administration.models import Product
from production.models import ChickenHouse, Farm

from .models import InventoryScope, ProductConsumptionConfig, ProductInventoryBalance
from .services import InventoryService

UserModel = get_user_model()


class ScopeResolutionMixin:
    def _resolve_scope_fields(self, cleaned_data: dict) -> tuple[str, Farm | None, ChickenHouse | None]:
        chicken_house = cleaned_data.get("chicken_house")
        farm = cleaned_data.get("farm")
        if chicken_house:
            return InventoryScope.CHICKEN_HOUSE, chicken_house.farm, chicken_house
        if farm:
            return InventoryScope.FARM, farm, None
        return InventoryScope.COMPANY, None, None


class ManualConsumptionForm(ScopeResolutionMixin, forms.Form):
    product = forms.ModelChoiceField(
        label="Producto",
        queryset=Product.objects.all().order_by("name"),
    )
    farm = forms.ModelChoiceField(
        label="Granja",
        queryset=Farm.objects.all().order_by("name"),
        required=False,
    )
    chicken_house = forms.ModelChoiceField(
        label="Galpón",
        queryset=ChickenHouse.objects.select_related("farm").order_by("farm__name", "name"),
        required=False,
    )
    quantity = forms.DecimalField(
        label="Cantidad",
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
    )
    executed_by = forms.ModelChoiceField(
        label="Colaborador que consumió",
        queryset=UserModel.objects.filter(is_active=True).order_by("apellidos", "nombres"),
        required=False,
    )
    notes = forms.CharField(
        label="Notas",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    def save(self, actor) -> None:
        cleaned = self.cleaned_data
        scope, farm, chicken_house = self._resolve_scope_fields(cleaned)
        service = InventoryService(actor=actor)
        service.register_manual_consumption(
            product=cleaned["product"],
            scope=scope,
            quantity=cleaned["quantity"],
            farm=farm,
            chicken_house=chicken_house,
            notes=cleaned.get("notes") or "",
            executed_by=cleaned.get("executed_by"),
        )


class InventoryResetForm(ScopeResolutionMixin, forms.Form):
    product = forms.ModelChoiceField(
        label="Producto",
        queryset=Product.objects.all().order_by("name"),
    )
    farm = forms.ModelChoiceField(
        label="Granja",
        queryset=Farm.objects.all().order_by("name"),
        required=False,
    )
    chicken_house = forms.ModelChoiceField(
        label="Galpón",
        queryset=ChickenHouse.objects.select_related("farm").order_by("farm__name", "name"),
        required=False,
    )
    physical_quantity = forms.DecimalField(
        label="Inventario físico",
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.00"),
    )
    notes = forms.CharField(
        label="Notas de conciliación",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    confirm_execution = forms.BooleanField(
        label="Confirmo que deseo resetear el inventario en este ámbito.",
        required=True,
        error_messages={"required": "Debes confirmar el reseteo."},
    )

    def save(self, actor) -> None:
        cleaned = self.cleaned_data
        scope, farm, chicken_house = self._resolve_scope_fields(cleaned)
        service = InventoryService(actor=actor)
        service.reset_scope(
            product=cleaned["product"],
            scope=scope,
            new_quantity=cleaned["physical_quantity"],
            farm=farm,
            chicken_house=chicken_house,
            notes=cleaned.get("notes") or "",
            metadata={"confirmed_by": getattr(actor, "pk", None)},
        )

    def current_balance(self) -> Decimal:
        if not self.is_bound or not self.is_valid():
            return Decimal("0.00")
        scope, farm, chicken_house = self._resolve_scope_fields(self.cleaned_data)
        balance = ProductInventoryBalance.objects.filter(
            product=self.cleaned_data["product"],
            scope=scope,
            farm=farm,
            chicken_house=chicken_house,
        ).first()
        return balance.quantity if balance else Decimal("0.00")


class ProductConsumptionConfigForm(ScopeResolutionMixin, forms.ModelForm):
    class Meta:
        model = ProductConsumptionConfig
        fields = ["farm", "chicken_house", "product", "start_date", "notes"]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 2}),
            "start_date": forms.DateInput(attrs={"type": "date"}),
        }

    def clean_product(self):
        product = self.cleaned_data["product"]
        if product.category != Product.Category.FOOD:
            raise forms.ValidationError("Solo puedes asociar productos de tipo alimento.")
        return product

    def clean(self):
        cleaned = super().clean()
        chicken_house = cleaned.get("chicken_house")
        farm = cleaned.get("farm")
        if chicken_house:
            cleaned["farm"] = chicken_house.farm
        elif not farm:
            self.add_error("farm", "Selecciona la granja o el galpón.")
        return cleaned

    def save(self, commit: bool = True):
        instance: ProductConsumptionConfig = super().save(commit=False)
        if instance.chicken_house_id:
            instance.scope = ProductConsumptionConfig.Scope.CHICKEN_HOUSE
            instance.farm = instance.chicken_house.farm
        else:
            instance.scope = ProductConsumptionConfig.Scope.FARM
            instance.chicken_house = None
        if commit:
            instance.save()
        return instance

    def clean(self):
        cleaned = super().clean()
        scope = cleaned.get("scope")
        farm = cleaned.get("farm")
        chicken_house = cleaned.get("chicken_house")
        if scope == ProductConsumptionConfig.Scope.FARM:
            if not farm:
                self.add_error("farm", "Selecciona la granja.")
            cleaned["chicken_house"] = None
        elif scope == ProductConsumptionConfig.Scope.CHICKEN_HOUSE:
            if not chicken_house:
                self.add_error("chicken_house", "Selecciona el galpón.")
            elif not farm:
                cleaned["farm"] = chicken_house.farm
        else:
            self.add_error("scope", "Selecciona un ámbito válido.")
        return cleaned
class InventoryFilterForm(forms.Form):
    product = forms.ModelChoiceField(
        label="Producto",
        queryset=Product.objects.all().order_by("name"),
        required=True,
    )
    farm = forms.ModelChoiceField(
        label="Granja",
        queryset=Farm.objects.all().order_by("name"),
        required=False,
    )
    chicken_house = forms.ModelChoiceField(
        label="Galpón",
        queryset=ChickenHouse.objects.select_related("farm").order_by("farm__name", "name"),
        required=False,
    )
    start_date = forms.DateField(
        label="Desde",
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    def clean(self):
        cleaned = super().clean()
        chicken_house = cleaned.get("chicken_house")
        if chicken_house:
            cleaned["farm"] = chicken_house.farm
        if not cleaned.get("start_date"):
            cleaned["start_date"] = timezone.localdate() - timedelta(days=30)
        return cleaned

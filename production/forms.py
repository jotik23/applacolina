from __future__ import annotations

from typing import Any

from django import forms

from production.models import ChickenHouse, Farm, Room


class BaseInfrastructureForm(forms.ModelForm):
    """Shared styling and behaviour for infrastructure administration forms."""

    input_classes = (
        "block w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm "
        "font-medium text-slate-700 shadow-inner transition focus:border-emerald-400 "
        "focus:outline-none focus:ring-2 focus:ring-emerald-100"
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            existing_classes = widget.attrs.get("class", "")
            widget.attrs["class"] = f"{existing_classes} {self.input_classes}".strip()
            widget.attrs.setdefault("placeholder", field.label)
            if isinstance(field, (forms.CharField, forms.EmailField)) and field.max_length:
                widget.attrs.setdefault("maxlength", str(field.max_length))
            if isinstance(field, forms.DecimalField):
                widget.attrs.setdefault("step", "0.01")
                widget.attrs.setdefault("min", "0")


class FarmForm(BaseInfrastructureForm):
    class Meta:
        model = Farm
        fields = ["name"]
        labels = {
            "name": "Nombre de la granja",
        }


class ChickenHouseForm(BaseInfrastructureForm):
    class Meta:
        model = ChickenHouse
        fields = ["farm", "name", "area_m2"]
        labels = {
            "farm": "Granja",
            "name": "Nombre del galpón",
            "area_m2": "Área del galpón (m²)",
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["farm"].queryset = Farm.objects.order_by("name")


class RoomForm(BaseInfrastructureForm):
    class Meta:
        model = Room
        fields = ["chicken_house", "name", "area_m2"]
        labels = {
            "chicken_house": "Galpón",
            "name": "Nombre del salón",
            "area_m2": "Área del salón (m²)",
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["chicken_house"].queryset = (
            ChickenHouse.objects.select_related("farm").order_by("farm__name", "name")
        )

from __future__ import annotations

from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

from django import forms
from django.db import transaction

from production.models import (
    BirdBatch,
    BirdBatchRoomAllocation,
    ChickenHouse,
    Farm,
    Room,
)


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
        fields = ["farm", "name"]
        labels = {
            "farm": "Granja",
            "name": "Nombre del galpón",
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


class BirdBatchForm(BaseInfrastructureForm):
    class Meta:
        model = BirdBatch
        fields = ["farm", "status", "birth_date", "initial_quantity", "breed"]
        labels = {
            "farm": "Granja",
            "status": "Estado",
            "birth_date": "Fecha de nacimiento",
            "initial_quantity": "Cantidad inicial de aves",
            "breed": "Raza",
        }
        widgets = {
            "birth_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["farm"].queryset = Farm.objects.order_by("name")
        self.fields["status"].widget.attrs.update(
            {"class": f"{self.input_classes} uppercase tracking-wide"}
        )


class BatchDistributionForm(forms.Form):
    """Bulk edition form for assigning bird batches across rooms."""

    input_classes = BaseInfrastructureForm.input_classes

    def __init__(self, *args: Any, batch: BirdBatch, **kwargs: Any) -> None:
        self.batch = batch
        self.room_metadata: Dict[str, Dict[str, Any]] = {}
        self.grouped_fields: "OrderedDict[ChickenHouse, List[str]]" = OrderedDict()
        self._clean_total: Optional[int] = None
        super().__init__(*args, **kwargs)

        allocations_by_room: Dict[int, BirdBatchRoomAllocation] = {
            allocation.room_id: allocation for allocation in batch.allocations.all()
        }

        rooms = (
            Room.objects.select_related("chicken_house", "chicken_house__farm")
            .filter(chicken_house__farm=batch.farm)
            .order_by("chicken_house__name", "name")
        )

        for room in rooms:
            field_name = f"room_{room.pk}"
            self.fields[field_name] = forms.IntegerField(
                required=False,
                min_value=0,
                label=room.name,
                widget=forms.NumberInput(
                    attrs={
                        "class": f"{self.input_classes} text-right",
                        "inputmode": "numeric",
                        "placeholder": "0",
                    }
                ),
            )
            allocation = allocations_by_room.get(room.pk)
            if allocation:
                self.fields[field_name].initial = allocation.quantity

            self.room_metadata[field_name] = {
                "room": room,
                "allocation": allocation,
            }
            self.grouped_fields.setdefault(room.chicken_house, []).append(field_name)

    def clean(self) -> Dict[str, Any]:
        cleaned_data = super().clean()
        total_assigned = 0
        for field_name in self.room_metadata:
            value = cleaned_data.get(field_name)
            if value:
                total_assigned += value

        if total_assigned > self.batch.initial_quantity:
            raise forms.ValidationError(
                "La suma asignada supera la cantidad inicial del lote. Ajusta los valores antes de guardar."
            )
        self._clean_total = total_assigned
        return cleaned_data

    def save(self) -> None:
        if not self.is_valid():
            raise ValueError("El formulario de distribución no es válido.")

        cleaned_data = self.cleaned_data
        with transaction.atomic():
            for field_name, metadata in self.room_metadata.items():
                room: Room = metadata["room"]
                allocation: Optional[BirdBatchRoomAllocation] = metadata["allocation"]
                value = cleaned_data.get(field_name)

                if not value:
                    if allocation:
                        allocation.delete()
                    continue

                if allocation:
                    if allocation.quantity != value:
                        allocation.quantity = value
                        allocation.save(update_fields=["quantity"])
                    continue

                BirdBatchRoomAllocation.objects.create(
                    bird_batch=self.batch,
                    room=room,
                    quantity=value,
                )

    def build_groups(self) -> Tuple[List[Dict[str, Any]], int]:
        groups: List[Dict[str, Any]] = []
        overall_total = 0

        initial_assignments_total = sum(
            allocation.quantity for allocation in self.batch.allocations.all()
        )
        for chicken_house, field_names in self.grouped_fields.items():
            fields: List[Dict[str, Any]] = []
            group_total = 0
            expanded = False

            for field_name in field_names:
                bound_field = self[field_name]
                value = self._coerce_value(bound_field.value())
                metadata = self.room_metadata[field_name]
                allocation: Optional[BirdBatchRoomAllocation] = metadata["allocation"]

                if value:
                    group_total += value
                    expanded = True

                if allocation and allocation.quantity:
                    expanded = True

                if bound_field.errors:
                    expanded = True

                allocated_before = allocation.quantity if allocation else 0
                proposed_total = initial_assignments_total - allocated_before + (value or 0)
                would_exceed = proposed_total > self.batch.initial_quantity

                fields.append(
                    {
                        "field": bound_field,
                        "room": metadata["room"],
                        "value": value,
                        "initial": allocation.quantity if allocation else None,
                        "proposed_total": proposed_total,
                        "would_exceed": would_exceed,
                    }
                )

            overall_total += group_total
            groups.append(
                {
                    "chicken_house": chicken_house,
                    "fields": fields,
                    "total": group_total,
                    "expanded": expanded,
                }
            )

        return groups, overall_total

    @staticmethod
    def _coerce_value(raw_value: Any) -> Optional[int]:
        if raw_value in (None, "", [], ()):
            return None
        try:
            return int(raw_value)
        except (TypeError, ValueError):
            return None

    @property
    def total_after_clean(self) -> Optional[int]:
        return self._clean_total

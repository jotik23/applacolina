from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Sequence, Tuple

from django import forms
from django.db import transaction

from production.models import (
    BirdBatch,
    BirdBatchRoomAllocation,
    ChickenHouse,
    Farm,
    Room,
)
from production.services.daily_board import RoomEntry


PRODUCTION_QUANTIZER = Decimal("1")


@dataclass(frozen=True)
class RoomProductionSnapshot:
    room_id: int
    room_name: str
    chicken_house_id: int
    chicken_house_name: str
    allocated_birds: int
    production: Optional[Decimal]
    consumption: Optional[Decimal]
    mortality: Optional[int]
    discard: Optional[int]


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


class BatchDailyProductionForm(forms.Form):
    """Matrix form used in the production board to capture per-room or per-barn entries."""

    date = forms.DateField(widget=forms.HiddenInput())
    average_egg_weight = forms.DecimalField(
        required=False,
        min_value=Decimal("0"),
        max_value=Decimal("999999"),
        decimal_places=0,
        widget=forms.NumberInput(
            attrs={
                "class": "block w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm "
                "font-semibold text-slate-700 shadow-inner transition focus:border-emerald-400 "
                "focus:outline-none focus:ring-2 focus:ring-emerald-100",
                "step": "1",
                "min": "0",
                "inputmode": "numeric",
                "pattern": "[0-9]*",
                "placeholder": "Peso promedio huevo (g)",
            }
        ),
        label="Peso promedio del huevo (g)",
    )

    def __init__(
        self,
        *,
        rooms: Sequence[RoomProductionSnapshot],
        input_mode: str = "rooms",
        data: Optional[Dict[str, Any]] = None,
        initial: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.rooms = list(rooms)
        self.input_mode = input_mode if input_mode in {"rooms", "barns"} else "rooms"
        self.room_field_map: dict[int, dict[str, str]] = {}
        self.barn_field_map: "OrderedDict[int, dict[str, str]]" = OrderedDict()
        self.barn_groups: "OrderedDict[int, dict[str, Any]]" = OrderedDict()
        self.room_rows: list[dict[str, Any]] = []
        self.barn_rows: list[dict[str, Any]] = []
        super().__init__(data=data, initial=initial)
        self._build_room_fields()
        self._build_barn_fields()

    def _build_room_fields(self) -> None:
        base_attrs = {
            "class": "w-full rounded-lg border border-slate-200 bg-white px-2 py-1 text-sm text-slate-700 "
            "shadow-inner transition focus:border-emerald-400 focus:outline-none focus:ring-2 "
            "focus:ring-emerald-100 text-right",
            "min": "0",
        }
        decimal_attrs = {
            **base_attrs,
            "step": "0.01",
            "inputmode": "decimal",
            "pattern": r"\d+(\.\d{1,2})?",
        }
        integer_attrs = {
            **base_attrs,
            "step": "1",
            "inputmode": "numeric",
        }
        wide_integer_attrs = {
            **integer_attrs,
            "class": f"{integer_attrs['class']} min-w-[6rem]",
        }

        for snapshot in self.rooms:
            field_prefix = f"room_{snapshot.room_id}"
            field_names: dict[str, str] = {}
            production_field = f"{field_prefix}_production"
            consumption_field = f"{field_prefix}_consumption"
            mortality_field = f"{field_prefix}_mortality"
            discard_field = f"{field_prefix}_discard"

            self.fields[production_field] = forms.IntegerField(
                required=False,
                min_value=0,
                widget=forms.NumberInput(attrs=wide_integer_attrs),
                label=f"{snapshot.room_name} · Producción (huevos)",
            )
            self.fields[production_field].initial = self.initial.get(production_field)
            field_names["production"] = production_field

            self.fields[consumption_field] = forms.DecimalField(
                required=False,
                min_value=Decimal("0"),
                decimal_places=2,
                max_digits=10,
                widget=forms.NumberInput(attrs=wide_integer_attrs),
                label=f"{snapshot.room_name} · Consumo",
            )
            self.fields[consumption_field].initial = self.initial.get(consumption_field)
            field_names["consumption"] = consumption_field

            self.fields[mortality_field] = forms.IntegerField(
                required=False,
                min_value=0,
                widget=forms.NumberInput(attrs=integer_attrs),
                label=f"{snapshot.room_name} · Mortalidad",
            )
            self.fields[mortality_field].initial = self.initial.get(mortality_field)
            field_names["mortality"] = mortality_field

            self.fields[discard_field] = forms.IntegerField(
                required=False,
                min_value=0,
                widget=forms.NumberInput(attrs=integer_attrs),
                label=f"{snapshot.room_name} · Descarte",
            )
            self.fields[discard_field].initial = self.initial.get(discard_field)
            field_names["discard"] = discard_field

            self.room_field_map[snapshot.room_id] = field_names
            self.room_rows.append(
                {
                    "room_id": snapshot.room_id,
                    "room_name": snapshot.room_name,
                    "chicken_house_name": snapshot.chicken_house_name,
                    "chicken_house_id": snapshot.chicken_house_id,
                    "allocated_birds": snapshot.allocated_birds,
                    "fields": {
                        metric: self[field_name] for metric, field_name in field_names.items()
                    },
                }
            )

            group = self.barn_groups.setdefault(
                snapshot.chicken_house_id,
                {
                    "name": snapshot.chicken_house_name,
                    "rooms": [],
                    "birds": 0,
                },
            )
            group["rooms"].append(snapshot)
            group["birds"] += snapshot.allocated_birds

    def _build_barn_fields(self) -> None:
        if not self.barn_groups:
            return

        base_attrs = {
            "class": "w-full rounded-lg border border-slate-200 bg-white px-2 py-1 text-sm text-slate-700 "
            "shadow-inner transition focus:border-emerald-400 focus:outline-none focus:ring-2 "
            "focus:ring-emerald-100 text-right",
            "min": "0",
        }
        decimal_attrs = {
            **base_attrs,
            "step": "0.01",
            "inputmode": "decimal",
            "pattern": r"\d+(\.\d{1,2})?",
        }
        integer_attrs = {
            **base_attrs,
            "step": "1",
            "inputmode": "numeric",
        }
        wide_integer_attrs = {
            **integer_attrs,
            "class": f"{integer_attrs['class']} min-w-[6rem]",
        }

        for barn_id, group in self.barn_groups.items():
            prefix = f"barn_{barn_id}"
            field_names: dict[str, str] = {}

            prod_field = f"{prefix}_production"
            cons_field = f"{prefix}_consumption"
            mort_field = f"{prefix}_mortality"
            disc_field = f"{prefix}_discard"

            self.fields[prod_field] = forms.IntegerField(
                required=False,
                min_value=0,
                widget=forms.NumberInput(attrs=wide_integer_attrs),
                label=f"{group['name']} · Producción (huevos)",
            )
            self.fields[prod_field].initial = self.initial.get(prod_field)
            field_names["production"] = prod_field

            self.fields[cons_field] = forms.DecimalField(
                required=False,
                min_value=Decimal("0"),
                decimal_places=2,
                max_digits=12,
                widget=forms.NumberInput(attrs=wide_integer_attrs),
                label=f"{group['name']} · Consumo",
            )
            self.fields[cons_field].initial = self.initial.get(cons_field)
            field_names["consumption"] = cons_field

            self.fields[mort_field] = forms.IntegerField(
                required=False,
                min_value=0,
                widget=forms.NumberInput(attrs=integer_attrs),
                label=f"{group['name']} · Mortalidad",
            )
            self.fields[mort_field].initial = self.initial.get(mort_field)
            field_names["mortality"] = mort_field

            self.fields[disc_field] = forms.IntegerField(
                required=False,
                min_value=0,
                widget=forms.NumberInput(attrs=integer_attrs),
                label=f"{group['name']} · Descarte",
            )
            self.fields[disc_field].initial = self.initial.get(disc_field)
            field_names["discard"] = disc_field

            self.barn_field_map[barn_id] = field_names
            self.barn_rows.append(
                {
                    "barn_id": barn_id,
                    "name": group["name"],
                    "birds": group["birds"],
                    "rooms": group["rooms"],
                    "fields": {
                        metric: self[field_name] for metric, field_name in field_names.items()
                    },
                }
            )

    def clean(self) -> Dict[str, Any]:
        cleaned_data = super().clean()
        if not self.rooms:
            self.add_error(None, "Configura los salones del lote antes de registrar producción.")
            return cleaned_data

        if self.input_mode == "barns":
            entries = self._build_entries_from_barns(cleaned_data)
        else:
            entries = self._build_entries_from_rooms(cleaned_data)

        self.cleaned_entries = entries
        return cleaned_data

    def _build_entries_from_rooms(self, cleaned_data: Dict[str, Any]) -> dict[int, RoomEntry]:
        entries: dict[int, RoomEntry] = {}
        for snapshot in self.rooms:
            field_names = self.room_field_map[snapshot.room_id]
            production_value = cleaned_data.get(field_names["production"])
            consumption_value = cleaned_data.get(field_names["consumption"])
            mortality_value = cleaned_data.get(field_names["mortality"]) or 0
            discard_value = cleaned_data.get(field_names["discard"]) or 0

            production = self._normalize_production_value(production_value)
            consumption = self._quantize_to_int(consumption_value)
            entries[snapshot.room_id] = RoomEntry(
                production=production,
                consumption=consumption,
                mortality=int(mortality_value),
                discard=int(discard_value),
            )
        return entries

    def _build_entries_from_barns(self, cleaned_data: Dict[str, Any]) -> dict[int, RoomEntry]:
        accumulator: dict[int, dict[str, Any]] = {
            snapshot.room_id: {
                "production": Decimal("0"),
                "consumption": 0,
                "mortality": 0,
                "discard": 0,
            }
            for snapshot in self.rooms
        }

        for barn_id, group in self.barn_groups.items():
            field_names = self.barn_field_map.get(barn_id, {})
            weights = [(room.room_id, room.allocated_birds or 0) for room in group["rooms"]]

            production_total = self._normalize_production_value(cleaned_data.get(field_names.get("production")))
            consumption_total = self._quantize_to_int(cleaned_data.get(field_names.get("consumption")))
            mortality_total = int(cleaned_data.get(field_names.get("mortality")) or 0)
            discard_total = int(cleaned_data.get(field_names.get("discard")) or 0)

            production_distribution = self._distribute_decimal_metric(production_total, weights)
            consumption_distribution = self._distribute_metric(consumption_total, weights)
            mortality_distribution = self._distribute_metric(mortality_total, weights)
            discard_distribution = self._distribute_metric(discard_total, weights)

            for room_id, _ in weights:
                accumulator[room_id]["production"] += production_distribution.get(room_id, Decimal("0"))
                accumulator[room_id]["consumption"] += consumption_distribution.get(room_id, 0)
                accumulator[room_id]["mortality"] += mortality_distribution.get(room_id, 0)
                accumulator[room_id]["discard"] += discard_distribution.get(room_id, 0)

        return {
            room_id: RoomEntry(
                production=data["production"],
                consumption=data["consumption"],
                mortality=data["mortality"],
                discard=data["discard"],
            )
            for room_id, data in accumulator.items()
        }

    def _distribute_metric(
        self,
        total: int,
        weights: Sequence[tuple[int, int]],
    ) -> dict[int, int]:
        if not weights:
            return {}
        if total <= 0:
            return {room_id: 0 for room_id, _ in weights}

        weight_sum = sum(weight for _, weight in weights)
        if weight_sum <= 0:
            base_share = total // len(weights)
            remainder = total - base_share * len(weights)
            distribution = {room_id: base_share for room_id, _ in weights}
            for room_id, _ in weights[:remainder]:
                distribution[room_id] += 1
            return distribution

        distribution: dict[int, int] = {}
        remainders: list[tuple[int, Decimal]] = []
        remaining = total
        for room_id, weight in weights:
            exact_share = (Decimal(weight) * Decimal(total)) / Decimal(weight_sum)
            integer_share = int(exact_share.to_integral_value(rounding=ROUND_DOWN))
            fraction = exact_share - Decimal(integer_share)
            distribution[room_id] = integer_share
            remaining -= integer_share
            remainders.append((room_id, fraction))

        remainders.sort(key=lambda item: item[1], reverse=True)
        for room_id, _fraction in remainders[:remaining]:
            distribution[room_id] += 1

        return distribution

    def _distribute_decimal_metric(
        self,
        total: Decimal,
        weights: Sequence[tuple[int, int]],
    ) -> dict[int, Decimal]:
        normalized_total = self._normalize_production_value(total)
        eggs_total = int(normalized_total)
        eggs_distribution = self._distribute_metric(eggs_total, weights)
        return {room_id: Decimal(value) for room_id, value in eggs_distribution.items()}

    def _quantize_to_int(self, value: Optional[Decimal]) -> int:
        if value in (None, ""):
            return 0
        return int(Decimal(value).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    def _normalize_production_value(self, value: Optional[Decimal]) -> Decimal:
        if value in (None, ""):
            return Decimal("0")
        quantized = Decimal(value).quantize(PRODUCTION_QUANTIZER, rounding=ROUND_HALF_UP)
        if quantized < 0:
            return Decimal("0")
        return quantized

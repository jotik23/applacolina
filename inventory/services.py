from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Iterable

from django.db import transaction
from django.utils import timezone

from administration.models import Product
from production.models import ChickenHouse, Farm, Room

from .models import (
    InventoryScope,
    ProductConsumptionConfig,
    ProductInventoryBalance,
    ProductInventoryEntry,
)


@dataclass(slots=True)
class InventoryReference:
    model_label: str
    instance_id: int | None = None

    @classmethod
    def from_instance(cls, instance) -> "InventoryReference":
        return cls(model_label=instance._meta.label_lower, instance_id=getattr(instance, "pk", None))


class InventoryService:
    def __init__(self, *, actor) -> None:
        self.actor = actor

    def register_receipt(
        self,
        *,
        product: Product,
        scope: str,
        quantity: Decimal,
        farm: Farm | None = None,
        chicken_house: ChickenHouse | None = None,
        notes: str = "",
        effective_date: date | None = None,
        reference: InventoryReference | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ProductInventoryEntry | None:
        if quantity == 0:
            return None
        with transaction.atomic():
            return self._apply_delta(
                product=product,
                scope=scope,
                delta=quantity,
                farm=farm,
                chicken_house=chicken_house,
                entry_type=ProductInventoryEntry.EntryType.RECEIPT,
                notes=notes,
                effective_date=effective_date,
                reference=reference,
                metadata=metadata,
            )

    def register_manual_consumption(
        self,
        *,
        product: Product,
        scope: str,
        quantity: Decimal,
        farm: Farm | None = None,
        chicken_house: ChickenHouse | None = None,
        notes: str = "",
        executed_by=None,
        effective_date: date | None = None,
        reference: InventoryReference | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ProductInventoryEntry | None:
        if quantity <= 0:
            return None
        with transaction.atomic():
            return self._apply_delta(
                product=product,
                scope=scope,
                delta=quantity * Decimal("-1"),
                farm=farm,
                chicken_house=chicken_house,
                entry_type=ProductInventoryEntry.EntryType.MANUAL_CONSUMPTION,
                notes=notes,
                effective_date=effective_date,
                reference=reference,
                metadata=metadata,
                executed_by=executed_by,
            )

    def reset_scope(
        self,
        *,
        product: Product,
        scope: str,
        new_quantity: Decimal,
        farm: Farm | None = None,
        chicken_house: ChickenHouse | None = None,
        notes: str = "",
        effective_date: date | None = None,
        reference: InventoryReference | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ProductInventoryEntry:
        with transaction.atomic():
            balance = self._get_balance(product, scope, farm, chicken_house, lock=True)
            previous = balance.quantity
            delta = new_quantity - previous
            metadata = metadata or {}
            metadata.update(
                {
                    "previous_balance": str(previous),
                    "reset_to": str(new_quantity),
                    "difference": str(delta),
                }
            )
            entry = self._apply_delta(
                product=product,
                scope=scope,
                delta=delta,
                farm=farm,
                chicken_house=chicken_house,
                entry_type=ProductInventoryEntry.EntryType.RESET,
                notes=notes,
                effective_date=effective_date,
                reference=reference,
                metadata=metadata,
            )
            return entry

    def consume_for_room_record(
        self,
        *,
        room: Room,
        product: Product,
        quantity: Decimal,
        effective_date: date,
        notes: str = "",
        reference: InventoryReference | None = None,
        recorded_by,
        metadata: dict[str, Any] | None = None,
    ) -> list[ProductInventoryEntry]:
        if quantity == 0:
            return []
        chicken_house = room.chicken_house
        farm = chicken_house.farm if chicken_house else None
        scope_order: list[tuple[str, Farm | None, ChickenHouse | None]] = []
        if chicken_house:
            scope_order.append((InventoryScope.CHICKEN_HOUSE, farm, chicken_house))
        if farm:
            scope_order.append((InventoryScope.FARM, farm, None))
        scope_order.append((InventoryScope.COMPANY, None, None))
        entries: list[ProductInventoryEntry] = []
        if quantity < 0:
            amount = abs(quantity)
            scope_name, scope_farm, scope_chicken = scope_order[0]
            with transaction.atomic():
                entry = self._apply_delta(
                    product=product,
                    scope=scope_name,
                    delta=amount,
                    farm=scope_farm,
                    chicken_house=scope_chicken,
                    entry_type=ProductInventoryEntry.EntryType.ADJUSTMENT,
                    notes=notes or "Ajuste por modificación del registro de producción",
                    effective_date=effective_date,
                    reference=reference,
                    metadata=metadata,
                    recorded_by=recorded_by,
                )
            return [entry]
        remaining = quantity
        shortage_entry_scope: tuple[str, Farm | None, ChickenHouse | None] | None = None
        with transaction.atomic():
            for scope_name, scope_farm, scope_chicken in scope_order:
                if remaining <= 0:
                    break
                balance = self._get_balance(product, scope_name, scope_farm, scope_chicken, lock=True)
                available = max(balance.quantity, Decimal("0.00"))
                consume_amount = min(remaining, available)
                if consume_amount > 0:
                    entry = self._apply_delta(
                        product=product,
                        scope=scope_name,
                        delta=consume_amount * Decimal("-1"),
                        farm=scope_farm,
                        chicken_house=scope_chicken,
                        entry_type=ProductInventoryEntry.EntryType.CONSUMPTION,
                        notes=notes,
                        effective_date=effective_date,
                        reference=reference,
                        metadata=metadata,
                        recorded_by=recorded_by,
                    )
                    entries.append(entry)
                    remaining -= consume_amount
                shortage_entry_scope = (scope_name, scope_farm, scope_chicken)
            if remaining > 0 and shortage_entry_scope:
                shortage_metadata = {"shortage": True}
                if metadata:
                    shortage_metadata.update(metadata)
                entry = self._apply_delta(
                    product=product,
                    scope=shortage_entry_scope[0],
                    delta=remaining * Decimal("-1"),
                    farm=shortage_entry_scope[1],
                    chicken_house=shortage_entry_scope[2],
                    entry_type=ProductInventoryEntry.EntryType.CONSUMPTION,
                    notes=notes or "Consumo con inventario insuficiente",
                    effective_date=effective_date,
                    reference=reference,
                    metadata=shortage_metadata,
                    recorded_by=recorded_by,
                )
                entries.append(entry)
        return entries

    def _apply_delta(
        self,
        *,
        product: Product,
        scope: str,
        delta: Decimal,
        farm: Farm | None,
        chicken_house: ChickenHouse | None,
        entry_type: str,
        notes: str,
        effective_date: date | None,
        reference: InventoryReference | None,
        metadata: dict[str, Any] | None,
        recorded_by=None,
        executed_by=None,
    ) -> ProductInventoryEntry:
        balance = self._get_balance(product, scope, farm, chicken_house, lock=True)
        new_quantity = balance.quantity + delta
        balance.quantity = new_quantity
        balance.save(update_fields=("quantity", "updated_at"))
        quantity_in = delta if delta > 0 else Decimal("0.00")
        quantity_out = abs(delta) if delta < 0 else Decimal("0.00")
        entry = ProductInventoryEntry(
            product=product,
            entry_type=entry_type,
            scope=scope,
            farm=farm,
            chicken_house=chicken_house,
            quantity_in=quantity_in,
            quantity_out=quantity_out,
            balance_after=new_quantity,
            notes=notes,
            recorded_by=recorded_by or self.actor,
            executed_by=executed_by,
            effective_date=effective_date or timezone.now().date(),
            data=metadata or {},
        )
        if reference and reference.instance_id:
            entry.reference_type = reference.model_label
            entry.reference_id = reference.instance_id
        entry.save()
        return entry

    def _get_balance(
        self,
        product: Product,
        scope: str,
        farm: Farm | None,
        chicken_house: ChickenHouse | None,
        *,
        lock: bool = False,
    ) -> ProductInventoryBalance:
        qs = ProductInventoryBalance.objects
        if lock:
            qs = qs.select_for_update()
        balance, _ = qs.get_or_create(
            product=product,
            scope=scope,
            farm=farm,
            chicken_house=chicken_house,
            defaults={"quantity": Decimal("0.00")},
        )
        return balance


def resolve_product_for_room(room: Room, *, target_date: date) -> Product | None:
    chicken_house = room.chicken_house
    farm = chicken_house.farm if chicken_house else None
    qs = ProductConsumptionConfig.objects.select_related("product")
    if chicken_house:
        config = (
            qs.filter(
                scope=ProductConsumptionConfig.Scope.CHICKEN_HOUSE,
                chicken_house=chicken_house,
                start_date__lte=target_date,
            )
            .order_by("-start_date")
            .first()
        )
        if config:
            return config.product
    if farm:
        config = (
            qs.filter(
                scope=ProductConsumptionConfig.Scope.FARM,
                farm=farm,
                start_date__lte=target_date,
            )
            .order_by("-start_date")
            .first()
        )
        if config:
            return config.product
    return None

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, Iterable, Optional

from django.db.models import Sum

from administration.models import Sale, SaleItem, SaleProductType
from production.models import EggDispatchDestination, EggDispatchItem, EggType
from production.services.egg_classification import ORDERED_EGG_TYPES


SALE_EGG_TYPE_MAP: Dict[str, str] = {
    SaleProductType.JUMBO: EggType.JUMBO,
    SaleProductType.TRIPLE_A: EggType.TRIPLE_A,
    SaleProductType.DOUBLE_A: EggType.DOUBLE_A,
    SaleProductType.SINGLE_A: EggType.SINGLE_A,
    SaleProductType.B: EggType.B,
    SaleProductType.C: EggType.C,
    SaleProductType.D: EggType.D,
}

SALE_PRODUCT_ORDER: tuple[str, ...] = (
    SaleProductType.JUMBO,
    SaleProductType.TRIPLE_A,
    SaleProductType.DOUBLE_A,
    SaleProductType.SINGLE_A,
    SaleProductType.B,
    SaleProductType.C,
    SaleProductType.D,
    SaleProductType.HEN,
    SaleProductType.HEN_MANURE,
)


@dataclass(frozen=True)
class WarehouseInventory:
    seller_id: int
    seller_name: str
    destination: str
    destination_label: str
    dispatched: Dict[str, Decimal] = field(default_factory=dict)
    sold: Dict[str, Decimal] = field(default_factory=dict)

    @property
    def available(self) -> Dict[str, Decimal]:
        data: Dict[str, Decimal] = {}
        for egg_type in ORDERED_EGG_TYPES:
            dispatched_total = self.dispatched.get(egg_type, Decimal("0"))
            sold_total = self.sold.get(egg_type, Decimal("0"))
            balance = dispatched_total - sold_total
            if balance < Decimal("0"):
                balance = Decimal("0")
            data[egg_type] = balance
        return data

    @property
    def total_available_cartons(self) -> Decimal:
        return sum(self.available.values(), Decimal("0"))

    def ordered_available(self) -> list[tuple[str, Decimal]]:
        data = self.available
        return [(egg_type, data.get(egg_type, Decimal("0"))) for egg_type in ORDERED_EGG_TYPES]

def build_warehouse_inventories(*, seller_ids: Optional[Iterable[int]] = None) -> list[WarehouseInventory]:
    seller_filter = list(seller_ids) if seller_ids else None
    dispatch_qs = EggDispatchItem.objects.select_related("dispatch__seller")
    if seller_filter:
        dispatch_qs = dispatch_qs.filter(dispatch__seller_id__in=seller_filter)
    dispatch_totals: Dict[tuple[int, str], Dict[str, Decimal]] = {}
    seller_names: Dict[int, str] = {}
    for item in dispatch_qs:
        dispatch = item.dispatch
        key = (dispatch.seller_id, dispatch.destination)
        seller = dispatch.seller
        if seller and seller.pk not in seller_names:
            seller_names[seller.pk] = seller.get_full_name() or seller.get_username()
        type_totals = dispatch_totals.setdefault(key, {})
        current = type_totals.get(item.egg_type, Decimal("0"))
        type_totals[item.egg_type] = current + Decimal(item.cartons or 0)

    sale_qs = SaleItem.objects.select_related("sale__seller").filter(
        sale__status__in=[Sale.Status.CONFIRMED, Sale.Status.PAID],
        sale__warehouse_destination__isnull=False,
    )
    if seller_filter:
        sale_qs = sale_qs.filter(sale__seller_id__in=seller_filter)

    sold_totals: Dict[tuple[int, str], Dict[str, Decimal]] = {}
    for item in sale_qs:
        egg_type = SALE_EGG_TYPE_MAP.get(item.product_type)
        if not egg_type:
            continue
        key = (item.sale.seller_id, item.sale.warehouse_destination)
        seller = item.sale.seller
        if seller and seller.pk not in seller_names:
            seller_names[seller.pk] = seller.get_full_name() or seller.get_username()
        sold_map = sold_totals.setdefault(key, {})
        current = sold_map.get(egg_type, Decimal("0"))
        sold_map[egg_type] = current + Decimal(item.quantity or 0)

    inventories: list[WarehouseInventory] = []
    destination_labels = dict(EggDispatchDestination.choices)
    seen_keys = set(dispatch_totals) | set(sold_totals)
    for seller_id, destination in sorted(seen_keys):
        dispatched_map = dispatch_totals.get((seller_id, destination), {})
        sold_map = sold_totals.get((seller_id, destination), {})
        inventories.append(
            WarehouseInventory(
                seller_id=seller_id,
                seller_name=seller_names.get(seller_id, "Vendedor"),
                destination=destination,
                destination_label=destination_labels.get(destination, destination),
                dispatched=dispatched_map,
                sold=sold_map,
            )
        )
    return inventories


def get_inventory_for_seller_destination(
    *,
    seller_id: Optional[int],
    destination: Optional[str],
    exclude_sale_id: Optional[int] = None,
) -> Dict[str, Decimal]:
    inventory: Dict[str, Decimal] = {egg_type: Decimal("0") for egg_type in ORDERED_EGG_TYPES}
    if not seller_id or not destination:
        return inventory

    dispatch_rows = (
        EggDispatchItem.objects.filter(dispatch__seller_id=seller_id, dispatch__destination=destination)
        .values("egg_type")
        .annotate(total=Sum("cartons"))
    )
    for row in dispatch_rows:
        egg_type = row.get("egg_type")
        if egg_type:
            inventory[egg_type] = Decimal(row.get("total") or 0)

    sale_rows = SaleItem.objects.filter(
        sale__seller_id=seller_id,
        sale__warehouse_destination=destination,
        sale__status__in=[Sale.Status.CONFIRMED, Sale.Status.PAID],
    )
    if exclude_sale_id:
        sale_rows = sale_rows.exclude(sale_id=exclude_sale_id)
    sale_totals = sale_rows.values("product_type").annotate(total=Sum("quantity"))
    for row in sale_totals:
        egg_type = SALE_EGG_TYPE_MAP.get(row.get("product_type"))
        if not egg_type:
            continue
        inventory[egg_type] -= Decimal(row.get("total") or 0)

    for egg_type in inventory:
        if inventory[egg_type] < Decimal("0"):
            inventory[egg_type] = Decimal("0")
    return inventory


def recalculate_sale_totals(sale: Sale) -> Sale:
    _refresh_payment_state(sale)
    return sale


def _refresh_payment_state(sale: Sale) -> None:
    balance = sale.balance_due
    new_status = sale.status
    paid_at = sale.paid_at
    if sale.status in (Sale.Status.CONFIRMED, Sale.Status.PAID):
        if balance <= Decimal("0"):
            new_status = Sale.Status.PAID
            if not paid_at:
                paid_at = sale.updated_at
        else:
            new_status = Sale.Status.CONFIRMED
            paid_at = None
    if new_status != sale.status or paid_at != sale.paid_at:
        sale.status = new_status
        sale.paid_at = paid_at
        sale.save(update_fields=["status", "paid_at", "updated_at"])


def refresh_sale_payment_state(sale: Sale) -> None:
    _refresh_payment_state(sale)

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Dict, Iterable, List, Optional, Tuple

from django.db.models import Sum

from administration.models import Sale, SaleItem, SaleProductType
from production.models import EggDispatch, EggDispatchDestination, EggDispatchItem, EggType
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

EGG_TO_SALE_PRODUCT_MAP: Dict[str, str] = {egg_type: sale_product for sale_product, egg_type in SALE_EGG_TYPE_MAP.items()}

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

CARDEX_DEFAULT_PRODUCT_ORDER: tuple[str, ...] = tuple(
    product for product in SALE_PRODUCT_ORDER if product in SALE_EGG_TYPE_MAP
)

DECIMAL_ZERO = Decimal("0.00")


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


@dataclass
class DispatchCardexSaleSummary:
    sale: Sale
    quantities_by_type: Dict[str, Decimal] = field(default_factory=dict)
    amounts_by_type: Dict[str, Decimal] = field(default_factory=dict)
    total_amount: Decimal = DECIMAL_ZERO
    payments_total: Decimal = DECIMAL_ZERO
    first_payment_date: Optional[date] = None
    last_payment_date: Optional[date] = None

    def add_allocation(self, product_type: str, quantity: Decimal, amount: Decimal) -> None:
        current_quantity = self.quantities_by_type.get(product_type, DECIMAL_ZERO)
        current_amount = self.amounts_by_type.get(product_type, DECIMAL_ZERO)
        self.quantities_by_type[product_type] = current_quantity + quantity
        self.amounts_by_type[product_type] = current_amount + amount
        self.total_amount += amount

    def register_payment(self, payment_date: date, amount: Decimal) -> None:
        self.payments_total += amount
        if amount <= DECIMAL_ZERO:
            return
        if not self.first_payment_date or payment_date < self.first_payment_date:
            self.first_payment_date = payment_date
        if not self.last_payment_date or payment_date > self.last_payment_date:
            self.last_payment_date = payment_date

    @property
    def balance(self) -> Decimal:
        balance = self.total_amount - self.payments_total
        if balance < DECIMAL_ZERO:
            return DECIMAL_ZERO
        return balance

    @property
    def total_quantity(self) -> Decimal:
        return sum(self.quantities_by_type.values(), DECIMAL_ZERO)


@dataclass
class DispatchCardexRow:
    dispatch: EggDispatch
    seller_id: int
    destination: str
    dispatched_by_type: Dict[str, Decimal] = field(default_factory=dict)
    sold_by_type: Dict[str, Decimal] = field(default_factory=dict)
    amount_by_type: Dict[str, Decimal] = field(default_factory=dict)
    sales: Dict[int, DispatchCardexSaleSummary] = field(default_factory=dict)
    payments_total: Decimal = DECIMAL_ZERO
    first_payment_date: Optional[date] = None
    last_payment_date: Optional[date] = None
    opening_balance: Dict[str, Decimal] = field(default_factory=dict)
    closing_balance: Dict[str, Decimal] = field(default_factory=dict)

    def register_payment_window(self, payment_date: date) -> None:
        if not self.first_payment_date or payment_date < self.first_payment_date:
            self.first_payment_date = payment_date
        if not self.last_payment_date or payment_date > self.last_payment_date:
            self.last_payment_date = payment_date

    @property
    def combo_key(self) -> Tuple[int, str]:
        return (self.seller_id, self.destination)

    @property
    def destination_label(self) -> str:
        return self.dispatch.get_destination_display()

    @property
    def total_dispatched(self) -> Decimal:
        return sum(self.dispatched_by_type.values(), DECIMAL_ZERO)

    @property
    def total_sold(self) -> Decimal:
        return sum(self.sold_by_type.values(), DECIMAL_ZERO)

    @property
    def total_amount(self) -> Decimal:
        return sum(self.amount_by_type.values(), DECIMAL_ZERO)

    @property
    def balance_due(self) -> Decimal:
        balance = self.total_amount - self.payments_total
        if balance < DECIMAL_ZERO:
            return DECIMAL_ZERO
        return balance

    @property
    def collection_duration_days(self) -> Optional[int]:
        if self.first_payment_date and self.last_payment_date:
            return (self.last_payment_date - self.first_payment_date).days
        return None

    @property
    def opening_total(self) -> Decimal:
        return sum(self.opening_balance.values(), DECIMAL_ZERO)

    @property
    def closing_total(self) -> Decimal:
        return sum(self.closing_balance.values(), DECIMAL_ZERO)

    def average_price(self, product_type: str) -> Decimal:
        sold = self.sold_by_type.get(product_type, DECIMAL_ZERO)
        amount = self.amount_by_type.get(product_type, DECIMAL_ZERO)
        if sold <= DECIMAL_ZERO:
            return DECIMAL_ZERO
        return (amount / sold).quantize(Decimal("0.01"))

    @property
    def average_price_by_type(self) -> Dict[str, Decimal]:
        prices: Dict[str, Decimal] = {}
        for product_type, amount in self.amount_by_type.items():
            sold = self.sold_by_type.get(product_type, DECIMAL_ZERO)
            if sold <= DECIMAL_ZERO:
                prices[product_type] = DECIMAL_ZERO
            else:
                prices[product_type] = (amount / sold).quantize(Decimal("0.01"))
        return prices

    def sorted_sales(self) -> List[DispatchCardexSaleSummary]:
        summaries = list(self.sales.values())
        summaries.sort(key=lambda summary: (summary.sale.date, summary.sale.pk), reverse=True)
        return summaries


@dataclass
class CardexUnassignedItem:
    sale: Sale
    product_type: str
    quantity: Decimal
    destination: Optional[str]
    reason: str


@dataclass
class SalesCardexResult:
    rows: List[DispatchCardexRow]
    ordered_product_types: List[str]
    unassigned_items: List[CardexUnassignedItem]

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


def build_sales_cardex(
    *,
    seller_ids: Optional[Iterable[int]] = None,
    destinations: Optional[Iterable[str]] = None,
    product_types: Optional[Iterable[str]] = None,
) -> SalesCardexResult:
    seller_filter = {seller_id for seller_id in seller_ids or [] if seller_id}
    destination_filter = {destination for destination in destinations or [] if destination}
    requested_types = [code for code in product_types or CARDEX_DEFAULT_PRODUCT_ORDER if code]
    ordered_product_types = [
        product_type for product_type in CARDEX_DEFAULT_PRODUCT_ORDER if product_type in requested_types
    ]
    if not ordered_product_types:
        ordered_product_types = list(CARDEX_DEFAULT_PRODUCT_ORDER)
    product_type_set = set(ordered_product_types)
    dispatch_qs = (
        EggDispatch.objects.select_related("seller", "driver")
        .prefetch_related("items")
        .order_by("date", "id")
    )
    if seller_filter:
        dispatch_qs = dispatch_qs.filter(seller_id__in=seller_filter)
    if destination_filter:
        dispatch_qs = dispatch_qs.filter(destination__in=destination_filter)
    dispatches = list(dispatch_qs)
    dispatch_rows: Dict[int, DispatchCardexRow] = {}
    for dispatch in dispatches:
        row = DispatchCardexRow(
            dispatch=dispatch,
            seller_id=dispatch.seller_id or 0,
            destination=dispatch.destination,
        )
        for item in dispatch.items.all():
            product_type = EGG_TO_SALE_PRODUCT_MAP.get(item.egg_type)
            if not product_type or product_type not in product_type_set:
                continue
            quantity = Decimal(item.cartons or 0)
            if quantity <= DECIMAL_ZERO:
                continue
            current = row.dispatched_by_type.get(product_type, DECIMAL_ZERO)
            row.dispatched_by_type[product_type] = current + quantity
        dispatch_rows[dispatch.pk] = row

    sale_qs = (
        Sale.objects.select_related("customer", "seller")
        .prefetch_related("items", "payments")
        .filter(status__in=[Sale.Status.CONFIRMED, Sale.Status.PAID])
        .exclude(warehouse_destination="")
        .exclude(warehouse_destination__isnull=True)
        .order_by("date", "pk")
    )
    if seller_filter:
        sale_qs = sale_qs.filter(seller_id__in=seller_filter)
    if destination_filter:
        sale_qs = sale_qs.filter(warehouse_destination__in=destination_filter)
    sales = list(sale_qs)

    events: list[tuple[date, int, int, tuple]] = []
    sequence = 0
    for dispatch in dispatches:
        events.append((dispatch.date, 0, sequence, ("dispatch", dispatch.pk)))
        sequence += 1
    for sale in sales:
        for item in sale.items.all():
            product_type = getattr(item, "product_type", "")
            if product_type not in product_type_set or product_type not in SALE_EGG_TYPE_MAP:
                continue
            quantity = Decimal(getattr(item, "quantity", 0) or 0)
            if quantity <= DECIMAL_ZERO:
                continue
            events.append((sale.date, 1, sequence, ("sale", sale, item)))
            sequence += 1

    events.sort(key=lambda entry: (entry[0], entry[1], entry[2]))
    stock_queues: Dict[tuple[int, str, str], deque] = defaultdict(deque)
    unassigned_items: List[CardexUnassignedItem] = []

    for _, _, _, payload in events:
        event_type = payload[0]
        if event_type == "dispatch":
            dispatch_id = payload[1]
            row = dispatch_rows.get(dispatch_id)
            if not row:
                continue
            for product_type, quantity in row.dispatched_by_type.items():
                if quantity <= DECIMAL_ZERO:
                    continue
                key = (row.seller_id, row.destination, product_type)
                stock_queues[key].append(
                    {
                        "dispatch_id": dispatch_id,
                        "remaining": quantity,
                    }
                )
        else:
            sale: Sale = payload[1]
            item: SaleItem = payload[2]
            product_type = item.product_type
            destination = sale.warehouse_destination or None
            if not destination:
                unassigned_items.append(
                    CardexUnassignedItem(
                        sale=sale,
                        product_type=product_type,
                        quantity=Decimal(item.quantity or 0),
                        destination=None,
                        reason="missing_destination",
                    )
                )
                continue

            queue_key = (sale.seller_id or 0, destination, product_type)
            queue = stock_queues.get(queue_key)
            remaining = Decimal(item.quantity or 0)
            unit_price = Decimal(item.unit_price or 0)
            if not queue:
                if remaining > DECIMAL_ZERO:
                    unassigned_items.append(
                        CardexUnassignedItem(
                            sale=sale,
                            product_type=product_type,
                            quantity=remaining,
                            destination=destination,
                            reason="missing_inventory",
                        )
                    )
                continue
            while remaining > DECIMAL_ZERO and queue:
                entry = queue[0]
                dispatch_id = entry["dispatch_id"]
                available = entry["remaining"]
                if available <= DECIMAL_ZERO:
                    queue.popleft()
                    continue
                allocated = min(remaining, available)
                entry["remaining"] = available - allocated
                if entry["remaining"] <= DECIMAL_ZERO:
                    queue.popleft()

                row = dispatch_rows.get(dispatch_id)
                if not row:
                    remaining -= allocated
                    continue
                sale_summary = row.sales.get(sale.pk)
                if not sale_summary:
                    sale_summary = DispatchCardexSaleSummary(sale=sale)
                    row.sales[sale.pk] = sale_summary
                amount = (allocated * unit_price).quantize(Decimal("0.01"))
                sale_summary.add_allocation(product_type, allocated, amount)
                current_sold = row.sold_by_type.get(product_type, DECIMAL_ZERO)
                row.sold_by_type[product_type] = current_sold + allocated
                current_amount = row.amount_by_type.get(product_type, DECIMAL_ZERO)
                row.amount_by_type[product_type] = current_amount + amount
                remaining -= allocated

            if remaining > DECIMAL_ZERO:
                unassigned_items.append(
                    CardexUnassignedItem(
                        sale=sale,
                        product_type=product_type,
                        quantity=remaining,
                        destination=destination,
                        reason="missing_inventory",
                    )
                )

    for row in dispatch_rows.values():
        for sale_summary in row.sales.values():
            sale_total = Decimal(sale_summary.sale.total_amount or 0)
            if sale_total <= DECIMAL_ZERO or sale_summary.total_amount <= DECIMAL_ZERO:
                continue
            payments = [
                payment for payment in sale_summary.sale.payments.all() if Decimal(payment.amount or 0) > DECIMAL_ZERO
            ]
            if not payments:
                continue
            ratio = sale_summary.total_amount / sale_total
            if ratio > Decimal("1"):
                ratio = Decimal("1")
            allocations: List[Decimal] = []
            payment_pairs: List[tuple] = []
            for payment in payments:
                payment_amount = Decimal(payment.amount or 0)
                raw_value = payment_amount * ratio
                payment_pairs.append((payment, raw_value))
                allocations.append(raw_value.quantize(Decimal("0.01")))
            allocated_total = sum(allocations, DECIMAL_ZERO)
            difference = sale_summary.total_amount - allocated_total
            if difference != DECIMAL_ZERO and allocations:
                allocations[-1] += difference
                if allocations[-1] < DECIMAL_ZERO:
                    allocations[-1] = DECIMAL_ZERO
            for (payment, _), allocated in zip(payment_pairs, allocations):
                if allocated <= DECIMAL_ZERO:
                    continue
                sale_summary.register_payment(payment.date, allocated)
                row.payments_total += allocated
                row.register_payment_window(payment.date)

    rows_ordered = sorted(dispatch_rows.values(), key=lambda row: (row.dispatch.date, row.dispatch.pk))
    running_balances: Dict[Tuple[int, str], Dict[str, Decimal]] = {}
    for row in rows_ordered:
        combo_balance = running_balances.setdefault(
            row.combo_key,
            {product_type: DECIMAL_ZERO for product_type in ordered_product_types},
        )
        row.opening_balance = {
            product_type: combo_balance.get(product_type, DECIMAL_ZERO) for product_type in ordered_product_types
        }
        for product_type in ordered_product_types:
            combo_balance[product_type] = combo_balance.get(product_type, DECIMAL_ZERO) + row.dispatched_by_type.get(
                product_type, DECIMAL_ZERO
            )
            combo_balance[product_type] -= row.sold_by_type.get(product_type, DECIMAL_ZERO)
            if combo_balance[product_type] < DECIMAL_ZERO:
                combo_balance[product_type] = DECIMAL_ZERO
        row.closing_balance = {
            product_type: combo_balance.get(product_type, DECIMAL_ZERO) for product_type in ordered_product_types
        }

    rows_descending = sorted(rows_ordered, key=lambda row: (row.dispatch.date, row.dispatch.pk), reverse=True)
    return SalesCardexResult(
        rows=rows_descending,
        ordered_product_types=list(ordered_product_types),
        unassigned_items=unassigned_items,
    )


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

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any, Iterator

from django.core.management.base import BaseCommand, CommandParser
from django.db import transaction

from inventory.models import ProductInventoryBalance, ProductInventoryEntry


class Command(BaseCommand):
    help = (
        "Recalcula los saldos del cardex y las tablas de balance para cada producto/"
        "ámbito en base a los movimientos registrados."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--product",
            type=int,
            help="ID de producto a recalcular. Si se omite se procesan todos.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        product_id: int | None = options.get("product")
        entries = ProductInventoryEntry.objects.select_related("product").order_by(
            "product_id",
            "scope",
            "farm_id",
            "chicken_house_id",
            "effective_date",
            "created_at",
            "pk",
        )
        if product_id:
            entries = entries.filter(product_id=product_id)
        updates = self._rebuild_entries(entries.iterator())
        self.stdout.write(self.style.SUCCESS(f"Movimientos actualizados: {updates['entries']}"))
        self.stdout.write(self.style.SUCCESS(f"Saldos actualizados: {updates['balances']}"))

    def _rebuild_entries(self, entries: Iterator[ProductInventoryEntry]) -> dict[str, int]:
        updated_entries = 0
        updated_balances = 0
        running_totals: dict[tuple[int, str, int | None, int | None], Decimal] = defaultdict(
            lambda: Decimal("0.00")
        )
        with transaction.atomic():
            for entry in entries:
                key = (entry.product_id, entry.scope, entry.farm_id, entry.chicken_house_id)
                running = running_totals[key]
                delta = entry.quantity_in - entry.quantity_out
                running += delta
                if entry.balance_after != running:
                    entry.balance_after = running
                    entry.save(update_fields=("balance_after", "updated_at"))
                    updated_entries += 1
                running_totals[key] = running
            for key, total in running_totals.items():
                product_id, scope, farm_id, chicken_house_id = key
                balance, _ = ProductInventoryBalance.objects.get_or_create(
                    product_id=product_id,
                    scope=scope,
                    farm_id=farm_id,
                    chicken_house_id=chicken_house_id,
                    defaults={"quantity": total},
                )
                if balance.quantity != total:
                    balance.quantity = total
                    balance.save(update_fields=("quantity", "updated_at"))
                    updated_balances += 1
        return {"entries": updated_entries, "balances": updated_balances}

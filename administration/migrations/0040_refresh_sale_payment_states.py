from __future__ import annotations

from decimal import Decimal

from django.db import migrations
from django.db.models import Sum


def refresh_sale_balances(apps, schema_editor):
    Sale = apps.get_model("administration", "Sale")
    SaleItem = apps.get_model("administration", "SaleItem")
    SalePayment = apps.get_model("administration", "SalePayment")
    zero = Decimal("0")
    penny = Decimal("0.01")

    for sale in Sale.objects.all().iterator(chunk_size=250):
        subtotal = (
            SaleItem.objects.filter(sale_id=sale.pk).aggregate(total=Sum("subtotal")).get("total") or zero
        )
        subtotal_amount = Decimal(subtotal)
        discount = Decimal(sale.discount_amount or zero)
        net_total = subtotal_amount - discount
        if net_total < zero:
            net_total = zero
        net_total = net_total.quantize(penny)

        payments_total = (
            SalePayment.objects.filter(sale_id=sale.pk).aggregate(total=Sum("amount")).get("total") or zero
        )
        payments_amount = Decimal(payments_total)
        balance = net_total - payments_amount
        if balance < zero:
            balance = zero

        new_status = sale.status
        new_paid_at = sale.paid_at
        if sale.status in ("confirmed", "paid"):
            if balance <= zero:
                new_status = "paid"
                if new_paid_at is None:
                    new_paid_at = sale.updated_at
            else:
                new_status = "confirmed"
                new_paid_at = None

        update_fields: list[str] = []
        if new_status != sale.status:
            sale.status = new_status
            update_fields.append("status")
        if new_paid_at != sale.paid_at:
            sale.paid_at = new_paid_at
            update_fields.append("paid_at")

        if update_fields:
            sale.save(update_fields=update_fields)


class Migration(migrations.Migration):

    dependencies = [
        ("administration", "0039_sale_invoice_fields"),
    ]

    operations = [
        migrations.RunPython(refresh_sale_balances, migrations.RunPython.noop),
    ]

from decimal import Decimal

from django.db import migrations


def recalc_estimated_totals(apps, schema_editor):
    PurchaseRequest = apps.get_model('administration', 'PurchaseRequest')
    PurchaseItem = apps.get_model('administration', 'PurchaseItem')
    for purchase in PurchaseRequest.objects.all():
        total = Decimal('0.00')
        items = PurchaseItem.objects.filter(purchase_id=purchase.pk)
        for item in items:
            quantity = item.quantity or Decimal('0.00')
            amount = item.estimated_amount or Decimal('0.00')
            total += quantity * amount
        if purchase.estimated_total != total:
            purchase.estimated_total = total
            purchase.save(update_fields=['estimated_total'])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('administration', '0025_alter_product_unit'),
    ]

    operations = [
        migrations.RunPython(recalc_estimated_totals, reverse_code=noop),
    ]

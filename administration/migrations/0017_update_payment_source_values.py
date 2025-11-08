from __future__ import annotations

from django.db import migrations


def forwards(apps, schema_editor):
    PurchaseRequest = apps.get_model('administration', 'PurchaseRequest')
    PurchaseRequest.objects.filter(payment_source__in=['operations', 'finance']).update(payment_source='treasury')
    PurchaseRequest.objects.filter(payment_source__isnull=True).update(payment_source='tbd')


def backwards(apps, schema_editor):
    # Cannot restore original values reliably.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('administration', '0016_purchaseitem_received_quantity_and_more'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]

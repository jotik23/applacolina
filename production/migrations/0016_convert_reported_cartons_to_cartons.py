from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from django.db import migrations


EGGS_PER_CARTON = Decimal("30")
CARTON_QUANTUM = Decimal("0.01")


def forward(apps, schema_editor):
    Batch = apps.get_model("production", "EggClassificationBatch")
    batches = (
        Batch.objects.select_related("production_record")
        .only("pk", "reported_cartons", "production_record__production")
        .iterator(chunk_size=500)
    )
    for batch in batches:
        record = batch.production_record
        if record is None or record.production is None:
            continue
        new_value = (Decimal(record.production) / EGGS_PER_CARTON).quantize(
            CARTON_QUANTUM,
            rounding=ROUND_HALF_UP,
        )
        if batch.reported_cartons == new_value:
            continue
        Batch.objects.filter(pk=batch.pk).update(reported_cartons=new_value)


def reverse(apps, schema_editor):
    Batch = apps.get_model("production", "EggClassificationBatch")
    batches = (
        Batch.objects.select_related("production_record")
        .only("pk", "production_record__production")
        .iterator(chunk_size=500)
    )
    for batch in batches:
        record = batch.production_record
        if record is None or record.production is None:
            continue
        Batch.objects.filter(pk=batch.pk).update(reported_cartons=record.production)


class Migration(migrations.Migration):
    dependencies = [
        ("production", "0015_backfill_egg_batches"),
    ]

    operations = [
        migrations.RunPython(forward, reverse_code=reverse),
    ]

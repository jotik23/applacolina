from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from django.db import migrations


MULTIPLIER = Decimal("30")
EGG_QUANTIZER = Decimal("1")
CARTON_QUANTIZER = Decimal("0.01")


def _apply_conversion(model, *, transform):
    queryset = model.objects.select_related(None).only("pk", "production")
    for record in queryset.iterator(chunk_size=500):
        if record.production is None:
            continue
        new_value = transform(record.production)
        if new_value == record.production:
            continue
        model.objects.filter(pk=record.pk).update(production=new_value)


def convert_cartons_to_eggs(apps, schema_editor):
    ProductionRecord = apps.get_model("production", "ProductionRecord")
    ProductionRoomRecord = apps.get_model("production", "ProductionRoomRecord")

    def to_eggs(value: Decimal) -> Decimal:
        return (value * MULTIPLIER).quantize(EGG_QUANTIZER, rounding=ROUND_HALF_UP)

    _apply_conversion(ProductionRecord, transform=to_eggs)
    _apply_conversion(ProductionRoomRecord, transform=to_eggs)


def convert_eggs_to_cartons(apps, schema_editor):
    ProductionRecord = apps.get_model("production", "ProductionRecord")
    ProductionRoomRecord = apps.get_model("production", "ProductionRoomRecord")

    def to_cartons(value: Decimal) -> Decimal:
        return (value / MULTIPLIER).quantize(CARTON_QUANTIZER, rounding=ROUND_HALF_UP)

    _apply_conversion(ProductionRecord, transform=to_cartons)
    _apply_conversion(ProductionRoomRecord, transform=to_cartons)


class Migration(migrations.Migration):
    dependencies = [
        ("production", "0011_weightsamplesession_production_room_record"),
    ]

    operations = [
        migrations.RunPython(convert_cartons_to_eggs, reverse_code=convert_eggs_to_cartons),
    ]

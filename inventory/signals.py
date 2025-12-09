from __future__ import annotations

from decimal import Decimal

from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from administration.models import Product
from production.models import ProductionRoomRecord

from .services import InventoryReference, InventoryService, resolve_product_for_room


@receiver(pre_save, sender=ProductionRoomRecord)
def cache_previous_consumption(sender, instance: ProductionRoomRecord, **kwargs) -> None:
    if instance.pk:
        previous = (
            ProductionRoomRecord.objects.filter(pk=instance.pk)
            .values_list("consumption", flat=True)
            .first()
        )
        instance._previous_consumption = previous or Decimal("0.00")
    else:
        instance._previous_consumption = Decimal("0.00")


@receiver(post_save, sender=ProductionRoomRecord)
def sync_inventory_on_save(sender, instance: ProductionRoomRecord, created: bool, **kwargs) -> None:
    previous_consumption = getattr(instance, "_previous_consumption", Decimal("0.00"))
    delta = instance.consumption - previous_consumption
    if delta == 0:
        return
    _apply_inventory_consumption(instance, delta)


@receiver(post_delete, sender=ProductionRoomRecord)
def restore_inventory_on_delete(sender, instance: ProductionRoomRecord, **kwargs) -> None:
    if instance.consumption:
        _apply_inventory_consumption(instance, instance.consumption * Decimal("-1"))


def _apply_inventory_consumption(instance: ProductionRoomRecord, delta: Decimal) -> None:
    production_record = instance.production_record
    product = resolve_product_for_room(instance.room, target_date=production_record.date)
    if not product:
        return
    product_category = getattr(product, "category", None)
    if product_category and product_category != Product.Category.FOOD:
        return
    reference = InventoryReference.from_instance(instance)
    metadata = {
        "production_record_id": production_record.pk,
        "room_id": instance.room_id,
        "bird_batch_id": production_record.bird_batch_id,
    }
    service = InventoryService(actor=production_record.updated_by or production_record.created_by)
    service.consume_for_room_record(
        room=instance.room,
        product=product,
        quantity=delta,
        effective_date=production_record.date,
        notes="Consumo registrado automáticamente",
        reference=reference,
        recorded_by=production_record.updated_by or production_record.created_by,
        metadata=metadata,
    )

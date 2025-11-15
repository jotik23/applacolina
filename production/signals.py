from django.db.models.signals import post_save
from django.dispatch import receiver

from production.models import ProductionRecord
from production.services.egg_classification import ensure_batch_for_record


@receiver(post_save, sender=ProductionRecord)
def ensure_classification_entry(sender, instance: ProductionRecord, **_kwargs) -> None:
    ensure_batch_for_record(instance)

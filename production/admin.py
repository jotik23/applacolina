from django.contrib import admin

from .models import ProductionRecord


@admin.register(ProductionRecord)
class ProductionRecordAdmin(admin.ModelAdmin):
    list_display = (
        "date",
        "bird_batch",
        "production",
        "consumption",
        "mortality",
        "discard",
    )
    list_filter = ("bird_batch", "date")
    search_fields = ("bird_batch__id", "bird_batch__farm__name", "date")
    ordering = ("-date",)

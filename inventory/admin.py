from django.contrib import admin

from .models import ProductConsumptionConfig, ProductInventoryBalance, ProductInventoryEntry


@admin.register(ProductInventoryBalance)
class ProductInventoryBalanceAdmin(admin.ModelAdmin):
    list_display = ("product", "scope", "farm", "chicken_house", "quantity", "updated_at")
    search_fields = ("product__name", "farm__name", "chicken_house__name")
    list_filter = ("scope",)


@admin.register(ProductInventoryEntry)
class ProductInventoryEntryAdmin(admin.ModelAdmin):
    list_display = (
        "effective_date",
        "product",
        "entry_type",
        "scope",
        "farm",
        "chicken_house",
        "quantity_in",
        "quantity_out",
        "balance_after",
    )
    search_fields = ("product__name", "notes")
    list_filter = ("entry_type", "scope")
    autocomplete_fields = ("product", "farm", "chicken_house", "recorded_by", "executed_by")


@admin.register(ProductConsumptionConfig)
class ProductConsumptionConfigAdmin(admin.ModelAdmin):
    list_display = ("scope", "farm", "chicken_house", "product", "start_date")
    search_fields = ("farm__name", "chicken_house__name", "product__name")
    list_filter = ("scope", "product")
    autocomplete_fields = ("farm", "chicken_house", "product", "created_by")

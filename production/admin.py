from django.contrib import admin
from django.core.exceptions import ValidationError
from django.db.models import Count
from django.forms import BaseInlineFormSet

from .models import (
    BirdBatch,
    BirdBatchRoomAllocation,
    ChickenHouse,
    Farm,
    ProductionRecord,
    Room,
)


class ChickenHouseInline(admin.TabularInline):
    model = ChickenHouse
    extra = 1


class RoomInline(admin.TabularInline):
    model = Room
    extra = 1


class BirdBatchRoomAllocationInlineFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return

        total_quantity = 0
        farm_id = getattr(self.instance, "farm_id", None)

        for form in self.forms:
            if form.cleaned_data.get("DELETE"):
                continue
            room = form.cleaned_data.get("room")
            quantity = form.cleaned_data.get("quantity") or 0

            if room and farm_id and room.chicken_house.farm_id != farm_id:
                raise ValidationError(
                    "Solo se pueden asignar salones pertenecientes a la misma granja del lote."
                )

            total_quantity += quantity

        initial_quantity = getattr(self.instance, "initial_quantity", 0) or 0
        if initial_quantity and total_quantity > initial_quantity:
            raise ValidationError(
                "La suma de aves asignadas no puede exceder la cantidad inicial del lote."
            )


class BirdBatchRoomAllocationInline(admin.TabularInline):
    model = BirdBatchRoomAllocation
    extra = 1
    formset = BirdBatchRoomAllocationInlineFormSet


class ProductionRecordInline(admin.TabularInline):
    model = ProductionRecord
    extra = 1
    fields = ("date", "production", "consumption", "mortality", "discard", "average_egg_weight")
    ordering = ("-date",)

    def save_model(self, request, obj, form, change):
        if not obj.created_by:
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(Farm)
class FarmAdmin(admin.ModelAdmin):
    inlines = (ChickenHouseInline,)
    list_display = ("name", "chicken_houses_count", "rooms_count")
    search_fields = ("name",)

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.annotate(
            chicken_houses_total=Count("chicken_houses", distinct=True),
            rooms_total=Count("chicken_houses__rooms", distinct=True),
        )

    def chicken_houses_count(self, obj):
        return obj.chicken_houses_total

    chicken_houses_count.short_description = "Galpones"
    chicken_houses_count.admin_order_field = "chicken_houses_total"

    def rooms_count(self, obj):
        return obj.rooms_total

    rooms_count.short_description = "Salones"
    rooms_count.admin_order_field = "rooms_total"


@admin.register(ChickenHouse)
class ChickenHouseAdmin(admin.ModelAdmin):
    inlines = (RoomInline,)
    list_display = ("name", "farm", "area_m2")
    search_fields = ("name", "farm__name")
    list_filter = ("farm",)


@admin.register(BirdBatch)
class BirdBatchAdmin(admin.ModelAdmin):
    inlines = (BirdBatchRoomAllocationInline, ProductionRecordInline)
    list_display = ("id", "farm", "status", "birth_date", "initial_quantity", "breed")
    search_fields = ("breed", "farm__name")
    list_filter = ("status", "farm")


@admin.register(ProductionRecord)
class ProductionRecordAdmin(admin.ModelAdmin):
    list_display = (
        "date",
        "bird_batch",
        "production",
        "consumption",
        "mortality",
        "discard",
        "average_egg_weight",
        "updated_by",
        "updated_at",
    )
    list_filter = ("bird_batch", "date")
    search_fields = ("bird_batch__id", "bird_batch__farm__name", "date")
    ordering = ("-date",)
    readonly_fields = ("created_by", "updated_by", "recorded_at", "updated_at")

    def save_model(self, request, obj, form, change):
        if not obj.created_by:
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ("name", "chicken_house", "area_m2")
    list_filter = ("chicken_house__farm", "chicken_house")
    search_fields = ("name", "chicken_house__name", "chicken_house__farm__name")

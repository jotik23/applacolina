from __future__ import annotations

from django.contrib import admin

from .models import (
    Product,
    PurchaseApproval,
    PurchaseItem,
    PurchaseReceptionAttachment,
    PurchaseRequest,
    PurchaseSupportAttachment,
    PurchasingExpenseType,
    Supplier,
    SupportDocumentType,
)
from production.models import ChickenHouse, Farm


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "tax_id",
        "bank_name",
        "account_number",
        "contact_name",
    )
    search_fields = ("name", "tax_id", "account_number", "bank_name")
    list_filter = ("bank_name",)
    fieldsets = (
        ("Identificación", {"fields": ("name", "tax_id")}),
        (
            "Contacto y ubicación",
            {
                "fields": (
                    "contact_name",
                    "contact_email",
                    "contact_phone",
                    "address",
                    "city",
                )
            },
        ),
        (
            "Datos bancarios",
            {
                "fields": (
                    "account_holder_id",
                    "account_holder_name",
                    "account_type",
                    "account_number",
                    "bank_name",
                )
            },
        ),
    )


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "unit", "category", "created_at")
    list_filter = ("category",)
    search_fields = ("name", "unit")


@admin.register(SupportDocumentType)
class SupportDocumentTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "requires_template")
    search_fields = ("name",)
    list_filter = ("kind",)


@admin.register(PurchasingExpenseType)
class PurchasingExpenseTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "parent_category", "iva_rate", "withholding_rate")
    search_fields = ("name", "parent_category__name")
    list_filter = ("parent_category",)
    autocomplete_fields = ("parent_category", "default_support_document_type")


class PurchaseItemInline(admin.TabularInline):
    model = PurchaseItem
    extra = 0
    fields = (
        "product",
        "description",
        "quantity",
        "estimated_amount",
        "received_quantity",
        "scope_area",
        "scope_farm",
        "scope_chicken_house",
    )
    autocomplete_fields = ("product", "scope_farm", "scope_chicken_house")


class PurchaseReceptionAttachmentInline(admin.TabularInline):
    model = PurchaseReceptionAttachment
    extra = 0
    fields = ("file", "notes", "uploaded_by", "created_at")
    readonly_fields = ("uploaded_by", "created_at")


class PurchaseSupportAttachmentInline(admin.TabularInline):
    model = PurchaseSupportAttachment
    extra = 0
    fields = ("file", "notes", "uploaded_by", "created_at")
    readonly_fields = ("uploaded_by", "created_at")


class PurchaseApprovalInline(admin.TabularInline):
    model = PurchaseApproval
    extra = 0
    fields = ("sequence", "role", "approver", "status", "decided_at", "comments")
    autocomplete_fields = ("approver",)
    readonly_fields = ("decided_at",)
    ordering = ("sequence",)


class PurchaseAreaFilter(admin.SimpleListFilter):
    title = "Área"
    parameter_name = "item_scope_area"

    def lookups(self, request, model_admin):
        return PurchaseRequest.AreaScope.choices

    def queryset(self, request, queryset):
        value = self.value()
        if value:
            return queryset.filter(items__scope_area=value).distinct()
        return queryset


class PurchaseFarmFilter(admin.SimpleListFilter):
    title = "Granja"
    parameter_name = "item_scope_farm"

    def lookups(self, request, model_admin):
        return [(str(farm.id), farm.name) for farm in Farm.objects.order_by("name")]

    def queryset(self, request, queryset):
        value = self.value()
        if value:
            return queryset.filter(items__scope_farm_id=value).distinct()
        return queryset


class PurchaseHouseFilter(admin.SimpleListFilter):
    title = "Galpón"
    parameter_name = "item_scope_house"

    def lookups(self, request, model_admin):
        return [
            (str(house.id), f"{house.farm.name if house.farm else ''} · {house.name}".strip(" ·"))
            for house in ChickenHouse.objects.select_related("farm").order_by("farm__name", "name")
        ]

    def queryset(self, request, queryset):
        value = self.value()
        if value:
            return queryset.filter(items__scope_chicken_house_id=value).distinct()
        return queryset


@admin.register(PurchaseRequest)
class PurchaseRequestAdmin(admin.ModelAdmin):
    list_display = (
        "timeline_code",
        "name",
        "expense_type",
        "supplier",
        "status",
        "scope_summary",
        "estimated_total",
        "created_at",
    )
    list_filter = (
        "status",
        PurchaseAreaFilter,
        "expense_type",
        "supplier",
        PurchaseFarmFilter,
        PurchaseHouseFilter,
        "payment_method",
        "payment_source",
    )
    search_fields = (
        "timeline_code",
        "name",
        "description",
        "supplier__name",
        "scope_batch_code",
        "order_number",
        "invoice_number",
    )
    autocomplete_fields = (
        "supplier",
        "expense_type",
        "requester",
        "assigned_manager",
        "support_document_type",
    )
    readonly_fields = (
        "scope_preview",
        "latest_approval_note_display",
        "created_at",
        "updated_at",
        "approved_at",
        "accounted_at",
    )
    fieldsets = (
        (
            "Detalles generales",
            {
                "fields": (
                    "timeline_code",
                    "name",
                    "description",
                    "status",
                    "expense_type",
                    "supplier",
                    "requester",
                    "assigned_manager",
                    "latest_approval_note_display",
                )
            },
        ),
        (
            "Alcance y logística",
            {
                "fields": (
                    "scope_batch_code",
                    "scope_preview",
                    "eta",
                    "purchase_date",
                    "order_number",
                    "order_date",
                    "delivery_condition",
                    "delivery_terms",
                    "shipping_eta",
                    "shipping_notes",
                    "reception_notes",
                    "reception_mismatch",
                )
            },
        ),
        (
            "Soporte y documentación",
            {
                "fields": (
                    "support_document_type",
                    "support_template_values",
                    "supplier_account_holder_id",
                    "supplier_account_holder_name",
                    "supplier_account_type",
                    "supplier_account_number",
                    "supplier_bank_name",
                )
            },
        ),
        (
            "Montos y pagos",
            {
                "fields": (
                    "estimated_total",
                    "currency",
                    "payment_condition",
                    "payment_method",
                    "payment_source",
                    "payment_amount",
                    "payment_account",
                    "payment_date",
                    "payment_notes",
                )
            },
        ),
        (
            "Factura y contabilización",
            {
                "fields": (
                    "invoice_number",
                    "invoice_date",
                    "invoice_total",
                    "accounted_in_system",
                    "accounted_at",
                    "approved_at",
                )
            },
        ),
        (
            "Metadatos",
            {
                "classes": ("collapse",),
                "fields": (
                    "created_at",
                    "updated_at",
                ),
            },
        ),
    )
    inlines = (
        PurchaseItemInline,
        PurchaseApprovalInline,
        PurchaseReceptionAttachmentInline,
        PurchaseSupportAttachmentInline,
    )
    date_hierarchy = "created_at"
    list_select_related = (
        "expense_type",
        "supplier",
        "scope_farm",
        "scope_chicken_house",
        "requester",
        "assigned_manager",
    )

    def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)
        for obj in instances:
            if isinstance(obj, (PurchaseReceptionAttachment, PurchaseSupportAttachment)) and not obj.uploaded_by:
                obj.uploaded_by = request.user
            obj.save()
        formset.save_m2m()
        for obj in formset.deleted_objects:
            obj.delete()

    @admin.display(description="Ubicación")
    def scope_summary(self, obj):
        if not obj:
            return ""
        return obj.scope_label

    @admin.display(description="Vista de alcance")
    def scope_preview(self, obj):
        if not obj:
            return ""
        return obj.scope_label or ""

    @admin.display(description="Última nota de aprobación")
    def latest_approval_note_display(self, obj):
        if not obj:
            return ""
        return obj.latest_approval_note or "—"

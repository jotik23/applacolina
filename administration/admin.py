from __future__ import annotations

from django.contrib import admin

from .models import Supplier


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

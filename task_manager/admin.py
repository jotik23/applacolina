from django import forms
from django.contrib import admin

from personal.models import DayOfWeek

from .models import TaskCategory, TaskDefinition, TaskStatus


class TaskDefinitionAdminForm(forms.ModelForm):
    weekly_days = forms.TypedMultipleChoiceField(
        label="Días de la semana",
        choices=DayOfWeek.choices,
        required=False,
        coerce=int,
        widget=forms.CheckboxSelectMultiple,
    )
    month_days = forms.TypedMultipleChoiceField(
        label="Días del mes",
        choices=[(day, day) for day in range(1, 32)],
        required=False,
        coerce=int,
        widget=forms.SelectMultiple,
    )

    class Meta:
        model = TaskDefinition
        fields = "__all__"


@admin.register(TaskStatus)
class TaskStatusAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name",)


@admin.register(TaskCategory)
class TaskCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "description")


@admin.register(TaskDefinition)
class TaskDefinitionAdmin(admin.ModelAdmin):
    form = TaskDefinitionAdminForm
    list_display = (
        "name",
        "task_type",
        "status",
        "category",
        "position",
        "collaborator",
    )
    list_filter = (
        "task_type",
        "status",
        "category",
        "position__farm",
    )
    search_fields = ("name", "description")
    filter_horizontal = ("farms", "chicken_houses", "rooms")
    autocomplete_fields = ("status", "category", "position", "collaborator")
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "name",
                    "description",
                    "status",
                    "category",
                    "task_type",
                )
            },
        ),
        (
            "Programación",
            {
                "fields": (
                    "scheduled_for",
                    "weekly_days",
                    "month_days",
                ),
                "classes": ("collapse",),
            },
        ),
        (
            "Asignaciones",
            {
                "fields": (
                    "position",
                    "collaborator",
                    "farms",
                    "chicken_houses",
                    "rooms",
                )
            },
        ),
        (
            "Trazabilidad",
            {"fields": ("created_at", "updated_at"), "classes": ("collapse",)},
        ),
    )

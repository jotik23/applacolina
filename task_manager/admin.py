from django import forms
from django.contrib import admin

from personal.models import DayOfWeek

from .models import TaskAssignment, TaskAssignmentEvidence, TaskCategory, TaskDefinition, TaskStatus


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
        "is_mandatory",
        "criticality_level",
        "position",
        "collaborator",
        "evidence_requirement",
        "record_format",
    )
    list_filter = (
        "task_type",
        "status",
        "category",
        "position__farm",
        "is_mandatory",
        "criticality_level",
        "evidence_requirement",
        "record_format",
    )
    search_fields = ("name", "description")
    filter_horizontal = ("rooms",)
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
                    "is_mandatory",
                    "criticality_level",
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
                    "rooms",
                )
            },
        ),
        (
            "Requisitos",
            {
                "fields": (
                    "evidence_requirement",
                    "record_format",
                )
            },
        ),
        (
            "Trazabilidad",
            {"fields": ("created_at", "updated_at"), "classes": ("collapse",)},
        ),
    )


class TaskAssignmentEvidenceInline(admin.TabularInline):
    model = TaskAssignmentEvidence
    extra = 0
    fields = ("file", "media_type", "note", "uploaded_by", "uploaded_at")
    readonly_fields = ("media_type", "uploaded_by", "uploaded_at")


@admin.register(TaskAssignment)
class TaskAssignmentAdmin(admin.ModelAdmin):
    list_display = (
        "task_definition",
        "collaborator",
        "due_date",
        "completed_on",
        "production_record",
    )
    list_filter = (
        "task_definition__category",
        "task_definition__status",
        "due_date",
        "completed_on",
    )
    search_fields = (
        "task_definition__name",
        "collaborator__nombres",
        "collaborator__apellidos",
    )
    date_hierarchy = "due_date"
    autocomplete_fields = ("task_definition", "collaborator", "production_record")
    readonly_fields = ("created_at", "updated_at")
    inlines = (TaskAssignmentEvidenceInline,)
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "task_definition",
                    "collaborator",
                    "previous_collaborator",
                    "due_date",
                )
            },
        ),
        (
            "Ejecución",
            {"fields": ("completed_on", "production_record")},
        ),
        (
            "Trazabilidad",
            {"fields": ("created_at", "updated_at"), "classes": ("collapse",)},
        ),
    )


@admin.register(TaskAssignmentEvidence)
class TaskAssignmentEvidenceAdmin(admin.ModelAdmin):
    list_display = ("assignment", "media_type", "uploaded_by", "uploaded_at")
    list_filter = ("media_type", "uploaded_at")
    search_fields = ("assignment__task_definition__name", "uploaded_by__nombres", "uploaded_by__apellidos")
    autocomplete_fields = ("assignment", "uploaded_by")
    readonly_fields = ("uploaded_at", "content_type", "file_size")

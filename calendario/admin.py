from __future__ import annotations

from django import forms
from django.contrib import admin
from django.db.models import QuerySet

from . import models


class PositionCategoryAdminForm(forms.ModelForm):
    class Meta:
        model = models.PositionCategory
        fields = "__all__"

    def __init__(self, *args, **kwargs) -> None:
        # Clarify how scheduling uses each numeric field.
        super().__init__(*args, **kwargs)
        labels = {
            "extra_day_limit": "Días de trabajo extra permitidos sobre el máximo",
            "overtime_points": "Puntos por cada día extra laborado",
            "rest_min_frequency": "Máximo de días laborados antes de sugerir descanso (UI)",
            "rest_min_consecutive_days": "Días de trabajo mínimo (UI)",
            "rest_max_consecutive_days": "Días de trabajo antes de descanso",
            "rest_post_shift_days": "Días post-turno",
            "rest_monthly_days": "Meta de días de descanso al mes (UI)",
        }
        for field_name, label in labels.items():
            if field_name in self.fields:
                self.fields[field_name].label = label


@admin.register(models.PositionDefinition)
class PositionDefinitionAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "name",
        "category",
        "farm",
        "complexity",
        "valid_from",
        "valid_until",
        "is_active",
    )
    list_filter = (
        "category",
        "complexity",
        "farm",
        "is_active",
    )
    search_fields = ("code", "name", "notes")
    list_editable = ("is_active",)
    date_hierarchy = "valid_from"

    def get_queryset(self, request) -> QuerySet:
        return (
            super()
            .get_queryset(request)
            .select_related("farm", "chicken_house", "category")
            .prefetch_related("rooms")
        )


@admin.register(models.PositionCategory)
class PositionCategoryAdmin(admin.ModelAdmin):
    form = PositionCategoryAdminForm
    fields = (
        "name",
        "code",
        "shift_type",
        "rest_max_consecutive_days",
        "extra_day_limit",
        "rest_post_shift_days",
        "overtime_points",
        "rest_min_frequency",
        "rest_min_consecutive_days",
        "rest_monthly_days",
        "overload_alert_level",
        "is_active",
    )
    list_display = (
        "name",
        "code",
        "shift_type",
        "extra_day_limit",
        "overtime_points",
        "overload_alert_level",
        "rest_max_consecutive_days",
        "is_active",
    )
    list_filter = ("shift_type", "is_active")
    search_fields = ("name", "code")


@admin.register(models.OperatorCapability)
class OperatorCapabilityAdmin(admin.ModelAdmin):
    list_display = (
        "operator",
        "category",
        "skill_score",
    )
    list_filter = ("category",)
    search_fields = (
        "operator__nombres",
        "operator__apellidos",
        "operator__cedula",
    )
    autocomplete_fields = ("operator",)


class AssignmentChangeLogInline(admin.TabularInline):
    model = models.AssignmentChangeLog
    extra = 0
    readonly_fields = (
        "changed_by",
        "change_type",
        "previous_operator",
        "new_operator",
        "details",
        "created_at",
    )


@admin.register(models.ShiftAssignment)
class ShiftAssignmentAdmin(admin.ModelAdmin):
    list_display = (
        "calendar",
        "date",
        "position",
        "operator",
        "is_auto_assigned",
        "alert_level",
        "is_overtime",
        "overtime_points",
    )
    list_filter = (
        "calendar",
        "position__category",
        "alert_level",
        "is_overtime",
        "is_auto_assigned",
    )
    search_fields = (
        "calendar__name",
        "position__code",
        "position__name",
        "operator__nombres",
        "operator__apellidos",
        "notes",
    )
    date_hierarchy = "date"
    autocomplete_fields = ("calendar", "position", "operator")
    inlines = (AssignmentChangeLogInline,)


class ShiftAssignmentInline(admin.TabularInline):
    model = models.ShiftAssignment
    extra = 0
    autocomplete_fields = ("position", "operator")
    readonly_fields = ("created_at", "updated_at")


@admin.register(models.ShiftCalendar)
class ShiftCalendarAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "start_date",
        "end_date",
        "status",
        "base_calendar",
        "created_by",
        "approved_by",
        "approved_at",
        "created_at",
    )
    list_filter = (
        "status",
        "created_by",
        "approved_by",
    )
    search_fields = (
        "name",
        "notes",
    )
    date_hierarchy = "start_date"
    autocomplete_fields = ("base_calendar", "created_by", "approved_by")
    inlines = (ShiftAssignmentInline,)


@admin.register(models.AssignmentChangeLog)
class AssignmentChangeLogAdmin(admin.ModelAdmin):
    list_display = (
        "assignment",
        "change_type",
        "changed_by",
        "created_at",
    )
    list_filter = ("change_type", "changed_by")
    search_fields = (
        "assignment__position__code",
        "assignment__operator__nombres",
        "assignment__operator__apellidos",
    )
    autocomplete_fields = ("assignment", "changed_by", "previous_operator", "new_operator")


@admin.register(models.WorkloadSnapshot)
class WorkloadSnapshotAdmin(admin.ModelAdmin):
    list_display = (
        "calendar",
        "operator",
        "month_reference",
        "total_shifts",
        "rest_days",
        "overtime_days",
        "overtime_points_total",
    )
    list_filter = ("month_reference",)
    autocomplete_fields = ("calendar", "operator")


@admin.register(models.OperatorRestPeriod)
class OperatorRestPeriodAdmin(admin.ModelAdmin):
    list_display = (
        "operator",
        "start_date",
        "end_date",
        "status",
        "source",
        "calendar",
    )
    list_filter = ("status", "source", "calendar")
    search_fields = (
        "operator__nombres",
        "operator__apellidos",
        "operator__cedula",
        "notes",
    )
    autocomplete_fields = ("operator", "calendar", "created_by")
    readonly_fields = ("created_at", "updated_at")

    def save_model(self, request, obj, form, change):
        if not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

from __future__ import annotations

from django.contrib import admin
from django.db.models import QuerySet

from . import models


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
            .select_related("farm", "chicken_house")
            .prefetch_related("rooms")
        )


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


class RestPreferenceInline(admin.TabularInline):
    model = models.RestPreference
    extra = 0


@admin.register(models.RestRule)
class RestRuleAdmin(admin.ModelAdmin):
    list_display = (
        "role",
        "shift_type",
        "min_rest_frequency",
        "min_consecutive_days",
        "max_consecutive_days",
        "post_shift_rest_days",
        "monthly_rest_days",
        "enforce_additional_rest",
        "active_from",
        "active_until",
    )
    list_filter = (
        "shift_type",
        "enforce_additional_rest",
        "role__name",
    )
    search_fields = (
        "role__name",
    )
    autocomplete_fields = ("role",)
    inlines = (RestPreferenceInline,)


@admin.register(models.OverloadAllowance)
class OverloadAllowanceAdmin(admin.ModelAdmin):
    list_display = (
        "role",
        "max_consecutive_extra_days",
        "highlight_level",
        "active_from",
        "active_until",
    )
    list_filter = ("highlight_level", "role__name")
    autocomplete_fields = ("role",)


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
    )
    list_filter = ("month_reference",)
    autocomplete_fields = ("calendar", "operator")

from __future__ import annotations

from django import forms
from django.contrib import admin, messages
from django.contrib.auth.admin import GroupAdmin
from django.contrib.auth.models import Group, Permission
from django.db.models import Q, QuerySet

from . import models
from .forms import UserChangeForm, UserCreationForm
from .models import UserGroup, UserProfile


try:
    admin.site.unregister(Group)
except admin.sites.NotRegistered:  # pragma: no cover - defensive
    pass

try:
    admin.site.unregister(Permission)
except admin.sites.NotRegistered:  # pragma: no cover - defensive
    pass


@admin.register(Permission)
class RolePermissionAdmin(admin.ModelAdmin):
    search_fields = ("name", "codename")
    list_display = ("name", "content_type", "codename")


@admin.register(UserGroup)
class UserGroupAdmin(GroupAdmin):
    """Expose auth groups under the personal module."""

    pass


class PositionCategoryAdminForm(forms.ModelForm):
    class Meta:
        model = models.PositionCategory
        fields = "__all__"

    def __init__(self, *args, **kwargs) -> None:
        # Clarify how scheduling uses each numeric field.
        super().__init__(*args, **kwargs)
        labels = {
            "rest_max_consecutive_days": "Días de trabajo antes de descanso",
            "rest_post_shift_days": "Días post-turno",
            "rest_monthly_days": "Meta de días de descanso al mes (UI)",
        }
        for field_name, label in labels.items():
            if field_name in self.fields:
                self.fields[field_name].label = label


class ActiveTodayFilter(admin.SimpleListFilter):
    title = "Activa hoy"
    parameter_name = "is_active_today"

    def lookups(self, request, model_admin):
        return (
            ("yes", "Sí"),
            ("no", "No"),
        )

    def queryset(self, request, queryset: QuerySet):
        reference_date = UserProfile.colombia_today()
        if self.value() == "yes":
            return queryset.active_on(reference_date)
        if self.value() == "no":
            inactive_filter = Q(valid_from__gt=reference_date) | Q(
                valid_until__lt=reference_date,
                valid_until__isnull=False,
            )
            return queryset.filter(inactive_filter)
        return queryset


@admin.register(models.PositionDefinition)
class PositionDefinitionAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "name",
        "category",
        "job_type_label",
        "farm",
        "valid_from",
        "valid_until",
        "is_active_today",
    )
    list_filter = (
        "category",
        "job_type",
        "farm",
        ActiveTodayFilter,
    )
    search_fields = ("code", "name")
    date_hierarchy = "valid_from"
    autocomplete_fields = ("handoff_position",)

    def get_queryset(self, request) -> QuerySet:
        return (
            super()
            .get_queryset(request)
            .select_related("farm", "chicken_house", "category", "handoff_position")
            .prefetch_related("rooms")
        )

    @admin.display(description="Activa hoy", boolean=True)
    def is_active_today(self, obj: models.PositionDefinition) -> bool:
        return obj.is_active_today()

    @admin.display(description="Tipo de puesto")
    def job_type_label(self, obj: models.PositionDefinition) -> str:
        return obj.get_job_type_display()


@admin.register(models.PositionCategory)
class PositionCategoryAdmin(admin.ModelAdmin):
    form = PositionCategoryAdminForm
    fields = (
        "display_name",
        "code",
        "shift_type",
        "rest_max_consecutive_days",
        "rest_post_shift_days",
        "rest_monthly_days",
        "is_active",
    )
    readonly_fields = ("display_name",)
    list_display = (
        "code",
        "display_name",
        "shift_type",
        "rest_max_consecutive_days",
        "is_active",
    )
    list_filter = ("shift_type", "is_active")
    search_fields = ("code",)

    @admin.display(description="Nombre")
    def display_name(self, obj: models.PositionCategory) -> str:
        return obj.display_name


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


class RolePermissionInline(admin.TabularInline):
    model = models.RolePermission
    extra = 0
    autocomplete_fields = ("permission",)
    fields = ("permission",)


@admin.register(models.Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("name", "permisos_count")
    search_fields = ("name",)
    inlines = [RolePermissionInline]

    def permisos_count(self, obj: models.Role) -> int:
        return obj.permissions.count()

    permisos_count.short_description = "Cantidad de permisos"


@admin.action(description="Activar usuarios seleccionados")
def activar_usuarios(modeladmin, request, queryset):
    actualizados = queryset.update(is_active=True)
    messages.success(request, f"{actualizados} usuarios activados.")


@admin.action(description="Desactivar usuarios seleccionados")
def desactivar_usuarios(modeladmin, request, queryset):
    actualizados = queryset.update(is_active=False)
    messages.success(request, f"{actualizados} usuarios desactivados.")


@admin.action(description="Restablecer clave generada aleatoriamente")
def resetear_clave(modeladmin, request, queryset):
    for usuario in queryset:
        password = UserProfile.objects.make_random_password()
        usuario.set_password(password)
        usuario.save(update_fields=["password"])
        messages.info(
            request,
            f"Clave temporal para {usuario.nombre_completo} ({usuario.cedula}): {password}",
        )


class ProfileActiveTodayFilter(admin.SimpleListFilter):
    title = "Activo hoy (Colombia)"
    parameter_name = "active_today"

    def lookups(self, request, model_admin):
        return (
            ("yes", "Activo hoy"),
            ("no", "Inactivo hoy"),
        )

    def queryset(self, request, queryset):
        today = UserProfile.colombia_today()
        active_condition = (
            (Q(employment_start_date__isnull=True) | Q(employment_start_date__lte=today))
            & (Q(employment_end_date__isnull=True) | Q(employment_end_date__gte=today))
        )

        if self.value() == "yes":
            return queryset.filter(active_condition)
        if self.value() == "no":
            return queryset.exclude(active_condition)
        return queryset


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    add_form = UserCreationForm
    form = UserChangeForm
    model = UserProfile

    list_display = (
        "cedula",
        "nombre_completo",
        "telefono",
        "automatic_rest_days_display",
        "listar_roles",
        "active_today",
        "is_staff",
    )
    list_filter = (ProfileActiveTodayFilter, "is_active", "is_staff", "roles")
    search_fields = ("cedula", "nombres", "apellidos", "telefono")
    ordering = ("apellidos", "nombres")

    fieldsets = (
        ("Credenciales", {"fields": ("cedula", "password")}),
        (
            "Informacion personal",
            {
                "fields": (
                    "nombres",
                    "apellidos",
                    "telefono",
                    "automatic_rest_days",
                    "suggested_positions",
                    "employment_start_date",
                    "employment_end_date",
                    "active_today",
                    "direccion",
                )
            },
        ),
        (
            "Contacto de emergencia",
            {"fields": ("contacto_nombre", "contacto_telefono")},
        ),
        (
            "Roles y permisos",
            {"fields": ("roles", "groups", "is_active", "is_staff", "is_superuser")},
        ),
        ("Fechas", {"fields": ("last_login", "date_joined")}),
    )

    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "cedula",
                    "nombres",
                    "apellidos",
                    "telefono",
                    "direccion",
                    "automatic_rest_days",
                    "suggested_positions",
                    "employment_start_date",
                    "employment_end_date",
                    "contacto_nombre",
                    "contacto_telefono",
                    "roles",
                    "groups",
                    "is_active",
                    "is_staff",
                    "password1",
                    "password2",
                ),
            },
        ),
    )
    filter_horizontal = ("groups", "user_permissions")
    actions = (activar_usuarios, desactivar_usuarios, resetear_clave)
    readonly_fields = ("last_login", "date_joined", "active_today")
    autocomplete_fields = ("suggested_positions",)

    @admin.display(description="Descansos automáticos")
    def automatic_rest_days_display(self, obj: UserProfile) -> str:
        labels = obj.automatic_rest_day_labels()
        return ", ".join(labels) if labels else "—"

    @admin.display(description="Roles")
    def listar_roles(self, obj: UserProfile) -> str:
        return ", ".join(role.get_name_display() for role in obj.roles.all()) or "—"

    @admin.display(description="Activo hoy", boolean=True)
    def active_today(self, obj: UserProfile) -> bool:
        return obj.is_active_today()

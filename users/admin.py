from __future__ import annotations

from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from calendario.models import OperatorRestPeriod
from .forms import UserChangeForm, UserCreationForm
from .models import Role, RolePermission, UserProfile


class RolePermissionInline(admin.TabularInline):
    model = RolePermission
    extra = 0


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("name", "permisos_count")
    search_fields = ("name",)
    inlines = [RolePermissionInline]

    def permisos_count(self, obj: Role) -> int:
        return obj.role_permissions.count()

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


class OperatorRestPeriodInline(admin.TabularInline):
    model = OperatorRestPeriod
    extra = 0
    autocomplete_fields = ("calendar",)
    fields = ("start_date", "end_date", "status", "source", "calendar")
    fk_name = "operator"


class ActiveTodayFilter(admin.SimpleListFilter):
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
class UserProfileAdmin(UserAdmin):
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
    list_filter = (ActiveTodayFilter, "is_active", "is_staff", "roles")
    search_fields = ("cedula", "nombres", "apellidos", "telefono")
    ordering = ("apellidos", "nombres")

    fieldsets = (
        (_("Credenciales"), {"fields": ("cedula", "password")}),
        (
            _("Informacion personal"),
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
            _("Contacto de emergencia"),
            {"fields": ("contacto_nombre", "contacto_telefono")},
        ),
        (
            _("Roles y permisos"),
            {"fields": ("roles", "groups", "is_active", "is_staff", "is_superuser")},
        ),
        (_("Fechas"), {"fields": ("last_login", "date_joined")}),
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

    filter_horizontal = ("roles", "groups", "user_permissions", "suggested_positions")
    readonly_fields = ("last_login", "date_joined", "active_today")

    actions = (activar_usuarios, desactivar_usuarios, resetear_clave)
    inlines = (OperatorRestPeriodInline,)

    @admin.display(boolean=True, description="Activo hoy (Colombia)")
    def active_today(self, obj: UserProfile | None) -> bool:
        if obj is None:
            return False
        return obj.is_active_today()

    def get_fieldsets(self, request, obj=None):
        fieldsets = super().get_fieldsets(request, obj)
        if not request.user.is_superuser:
            # Remove fields that non superusers cannot change.
            sanitized = []
            restricted_fields = {"is_superuser", "groups", "user_permissions"}
            for title, opts in fieldsets:
                fields = opts.get("fields")
                if isinstance(fields, (list, tuple)):
                    filtered = tuple(f for f in fields if f not in restricted_fields)
                else:
                    filtered = fields
                sanitized.append((title, {**opts, "fields": filtered}))
            return tuple(sanitized)
        return fieldsets

    def listar_roles(self, obj: UserProfile) -> str:
        return ", ".join(role.get_name_display() for role in obj.roles.all())

    listar_roles.short_description = "Roles"

    def automatic_rest_days_display(self, obj: UserProfile) -> str:
        labels = obj.automatic_rest_day_labels()
        return ", ".join(labels) if labels else "—"

    automatic_rest_days_display.short_description = "Descanso automático"

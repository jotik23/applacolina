from __future__ import annotations

from datetime import date

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .managers import UserProfileManager


COLOMBIA_TZ_NAME = "America/Bogota"


class Role(models.Model):
    class RoleName(models.TextChoices):
        GALPONERO = "GALPONERO", "Galponero"
        CLASIFICADOR = "CLASIFICADOR", "Clasificador"
        ADMINISTRADOR = "ADMINISTRADOR", "Administrador"
        SUPERVISOR = "SUPERVISOR", "Supervisor"

    name = models.CharField(
        max_length=32,
        unique=True,
        choices=RoleName.choices,
    )

    class Meta:
        verbose_name = "Rol"
        verbose_name_plural = "Roles"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.get_name_display()


class RolePermission(models.Model):
    class PermissionCode(models.TextChoices):
        VIEW_USERS = "view_users", "Ver usuarios"
        MANAGE_USERS = "manage_users", "Gestionar usuarios"
        VIEW_ROLES = "view_roles", "Ver roles"
        MANAGE_ROLES = "manage_roles", "Gestionar roles"

    role = models.ForeignKey(
        Role,
        on_delete=models.CASCADE,
        related_name="role_permissions",
    )
    permission_code = models.CharField(
        max_length=64,
        choices=PermissionCode.choices,
    )

    class Meta:
        verbose_name = "Permiso por rol"
        verbose_name_plural = "Permisos por rol"
        unique_together = ("role", "permission_code")
        ordering = ["role__name", "permission_code"]

    def __str__(self) -> str:
        return f"{self.role.get_name_display()} - {self.get_permission_code_display()}"


class RestDayOfWeek(models.IntegerChoices):
    MONDAY = 0, _("Lunes")
    TUESDAY = 1, _("Martes")
    WEDNESDAY = 2, _("Miércoles")
    THURSDAY = 3, _("Jueves")
    FRIDAY = 4, _("Viernes")
    SATURDAY = 5, _("Sábado")
    SUNDAY = 6, _("Domingo")


class UserProfile(AbstractBaseUser, PermissionsMixin):
    cedula = models.CharField(max_length=32, unique=True)
    nombres = models.CharField(max_length=150)
    apellidos = models.CharField(max_length=150)
    telefono = models.CharField(max_length=32, unique=True)
    direccion = models.CharField(max_length=255, blank=True)
    contacto_nombre = models.CharField(max_length=150, blank=True)
    contacto_telefono = models.CharField(max_length=32, blank=True)
    suggested_positions = models.ManyToManyField(
        "calendario.PositionDefinition",
        blank=True,
        related_name="preferred_operators",
        verbose_name="Posiciones sugeridas",
        help_text="Posiciones recomendadas para priorizar asignaciones automáticas.",
    )
    employment_start_date = models.DateField(
        "Fecha de ingreso",
        null=True,
        blank=True,
        help_text="Fecha desde la cual el colaborador se considera activo para turnos y descansos.",
    )
    employment_end_date = models.DateField(
        "Fecha de retiro",
        null=True,
        blank=True,
        help_text="Si se establece, el colaborador deja de estar disponible para turnos a partir del día siguiente.",
    )
    automatic_rest_days = ArrayField(
        base_field=models.PositiveSmallIntegerField(choices=RestDayOfWeek.choices),
        default=list,
        blank=True,
        verbose_name="Días de descanso automático",
        help_text="Bloquea las asignaciones automáticas en los días seleccionados.",
    )
    roles = models.ManyToManyField(Role, blank=True, related_name="usuarios")

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)

    objects = UserProfileManager()

    USERNAME_FIELD = "cedula"
    REQUIRED_FIELDS = ["nombres", "apellidos", "telefono"]

    class Meta:
        verbose_name = "Usuario"
        verbose_name_plural = "Usuarios"
        ordering = ["apellidos", "nombres"]

    def __str__(self) -> str:
        return f"{self.nombres} {self.apellidos} ({self.cedula})"

    @property
    def nombre_completo(self) -> str:
        return f"{self.nombres} {self.apellidos}".strip()

    def get_full_name(self) -> str:
        return self.nombre_completo

    def get_short_name(self) -> str:
        return self.nombres.split(" ")[0] if self.nombres else ""

    @staticmethod
    def colombia_today() -> date:
        current = timezone.now()
        try:
            localized = current.astimezone(ZoneInfo(COLOMBIA_TZ_NAME))
        except ZoneInfoNotFoundError:  # pragma: no cover - fallback when zoneinfo is unavailable
            localized = timezone.localtime(current)
        return localized.date()

    def is_active_on(self, target_date: date) -> bool:
        if not target_date:
            return False

        start_date = self.employment_start_date
        end_date = self.employment_end_date

        if start_date and target_date < start_date:
            return False
        if end_date and target_date > end_date:
            return False

        return True

    def is_active_today(self) -> bool:
        return self.is_active_on(self.colombia_today())

    def automatic_rest_day_labels(self) -> list[str]:
        if not self.automatic_rest_days:
            return []
        labels: list[str] = []
        for value in sorted(set(self.automatic_rest_days)):
            try:
                labels.append(str(RestDayOfWeek(value).label))
            except ValueError:
                labels.append(str(value))
        return labels

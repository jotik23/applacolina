from __future__ import annotations

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.utils import timezone

from .managers import UserProfileManager


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


class UserProfile(AbstractBaseUser, PermissionsMixin):
    cedula = models.CharField(max_length=32, unique=True)
    nombres = models.CharField(max_length=150)
    apellidos = models.CharField(max_length=150)
    telefono = models.CharField(max_length=32, unique=True)
    email = models.EmailField(blank=True)
    direccion = models.CharField(max_length=255, blank=True)
    contacto_nombre = models.CharField(max_length=150, blank=True)
    contacto_telefono = models.CharField(max_length=32, blank=True)
    preferred_farm = models.ForeignKey(
        "granjas.Farm",
        on_delete=models.SET_NULL,
        related_name="preferred_operators",
        verbose_name="Granja preferida",
        null=True,
        blank=True,
    )
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

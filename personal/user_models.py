from __future__ import annotations

from datetime import date

from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractBaseUser, Group, Permission, PermissionsMixin
from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


COLOMBIA_TZ_NAME = "America/Bogota"


class UserProfileQuerySet(models.QuerySet):
    """Custom queryset helpers for UserProfile."""

    def active_on(self, target_date: date) -> "UserProfileQuerySet":
        """Filter collaborators that are active on the given date."""

        if not target_date:
            return self.none()

        return self.filter(
            Q(employment_start_date__isnull=True) | Q(employment_start_date__lte=target_date),
            Q(employment_end_date__isnull=True) | Q(employment_end_date__gte=target_date),
        )


class UserProfileManager(BaseUserManager):
    """Custom manager for the UserProfile model."""

    use_in_migrations = True

    def get_queryset(self):  # type: ignore[override]
        return UserProfileQuerySet(self.model, using=self._db)

    def active_on(self, target_date: date):
        return self.get_queryset().active_on(target_date)

    def _create_user(self, cedula: str, password: str | None, **extra_fields):
        if not cedula:
            raise ValueError("El usuario debe tener una cedula definida.")
        cedula = cedula.strip()
        user = self.model(cedula=cedula, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_user(self, cedula: str, password: str | None = None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(cedula, password, **extra_fields)

    def create_superuser(self, cedula: str, password: str | None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Los superusuarios deben tener is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Los superusuarios deben tener is_superuser=True.")
        return self._create_user(cedula, password, **extra_fields)


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
        db_table = "users_role"

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
        db_table = "users_rolepermission"

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
        "personal.PositionDefinition",
        blank=True,
        related_name="preferred_operators",
        verbose_name="Posiciones sugeridas",
        help_text="Posiciones recomendadas para priorizar asignaciones automáticas.",
        db_table="users_userprofile_suggested_positions",
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
    roles = models.ManyToManyField(
        Role,
        blank=True,
        related_name="usuarios",
        db_table="users_userprofile_roles",
    )
    groups = models.ManyToManyField(
        Group,
        verbose_name=_("groups"),
        blank=True,
        help_text=_(
            "The groups this user belongs to. A user will get all permissions granted to each of their groups."
        ),
        related_name="user_set",
        related_query_name="user",
        db_table="users_userprofile_groups",
    )
    user_permissions = models.ManyToManyField(
        Permission,
        verbose_name=_("user permissions"),
        blank=True,
        help_text=_("Specific permissions for this user."),
        related_name="user_set",
        related_query_name="user",
        db_table="users_userprofile_user_permissions",
    )

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
        db_table = "users_userprofile"

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

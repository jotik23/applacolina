from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractBaseUser, Group, Permission, PermissionsMixin
from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Max
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from granjas.models import ChickenHouse, Farm, Room

COLOMBIA_TZ_NAME = "America/Bogota"


class UserProfileQuerySet(models.QuerySet):
    """Custom queryset helpers for UserProfile."""

    def active_on(self, target_date: date) -> "UserProfileQuerySet":
        """Filter collaborators that are active on the given date."""

        if not target_date:
            return self.none()

        return self.filter(
            models.Q(employment_start_date__isnull=True) | models.Q(employment_start_date__lte=target_date),
            models.Q(employment_end_date__isnull=True) | models.Q(employment_end_date__gte=target_date),
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


class UserGroup(Group):
    class Meta:
        proxy = True
        verbose_name = "Grupo"
        verbose_name_plural = "Grupos"
        app_label = "personal"


class ShiftType(models.TextChoices):
    DAY = "day", _("Día")
    NIGHT = "night", _("Noche")
    MIXED = "mixed", _("Mixto")


class AssignmentAlertLevel(models.TextChoices):
    NONE = "none", _("Sin alerta")
    WARN = "warn", _("Desajuste moderado")
    CRITICAL = "critical", _("Desajuste crítico")


class DayOfWeek(models.IntegerChoices):
    MONDAY = 0, _("Lunes")
    TUESDAY = 1, _("Martes")
    WEDNESDAY = 2, _("Miércoles")
    THURSDAY = 3, _("Jueves")
    FRIDAY = 4, _("Viernes")
    SATURDAY = 5, _("Sábado")
    SUNDAY = 6, _("Domingo")


class PositionCategoryCode(models.TextChoices):
    GALPONERO_PRODUCCION_DIA = "GALPONERO_PRODUCCION_DIA", _("Galponero producción día")
    GALPONERO_LEVANTE_DIA = "GALPONERO_LEVANTE_DIA", _("Galponero levante día")
    GALPONERO_PRODUCCION_NOCHE = "GALPONERO_PRODUCCION_NOCHE", _("Galponero producción noche")
    GALPONERO_LEVANTE_NOCHE = "GALPONERO_LEVANTE_NOCHE", _("Galponero levante noche")
    CLASIFICADOR_DIA = "CLASIFICADOR_DIA", _("Clasificador día")
    CLASIFICADOR_NOCHE = "CLASIFICADOR_NOCHE", _("Clasificador noche")
    LIDER_GRANJA = "LIDER_GRANJA", _("Líder de granja")
    SUPERVISOR = "SUPERVISOR", _("Supervisor")
    LIDER_TECNICO = "LIDER_TECNICO", _("Líder técnico")
    OFICIOS_VARIOS = "OFICIOS_VARIOS", _("Oficios varios")


class PositionCategory(models.Model):
    code = models.CharField(
        "Código",
        max_length=64,
        choices=PositionCategoryCode.choices,
        unique=True,
    )
    shift_type = models.CharField(
        "Turno",
        max_length=16,
        choices=ShiftType.choices,
        default=ShiftType.DAY,
    )
    rest_max_consecutive_days = models.PositiveSmallIntegerField(
        "Días de descanso consecutivos máximos",
        validators=[MinValueValidator(1)],
        default=8,
    )
    rest_post_shift_days = models.PositiveSmallIntegerField(
        "Descanso posterior al turno",
        default=0,
    )
    rest_monthly_days = models.PositiveSmallIntegerField(
        "Descanso mensual requerido",
        validators=[MinValueValidator(1)],
        default=5,
    )
    is_active = models.BooleanField("Activo", default=True)

    class Meta:
        verbose_name = "Categoría de posición"
        verbose_name_plural = "Categorías de posiciones"
        ordering = ("code",)
        db_table = "calendario_positioncategory"

    def __str__(self) -> str:
        return self.display_name

    @property
    def is_night_shift(self) -> bool:
        return self.shift_type == ShiftType.NIGHT

    @property
    def display_name(self) -> str:
        return self.get_code_display()


class CalendarStatus(models.TextChoices):
    DRAFT = "draft", _("Borrador")
    APPROVED = "approved", _("Aprobado")
    MODIFIED = "modified", _("Modificado")


class RestPeriodStatus(models.TextChoices):
    PLANNED = "planned", _("Planificado")
    APPROVED = "approved", _("Aprobado")
    CONFIRMED = "confirmed", _("Confirmado")
    EXPIRED = "expired", _("Expirado")
    CANCELLED = "cancelled", _("Cancelado")


class RestPeriodSource(models.TextChoices):
    MANUAL = "manual", _("Manual")
    CALENDAR = "calendar", _("Calendario")


class PositionDefinitionQuerySet(models.QuerySet):
    def active_on(self, target_date: date) -> "PositionDefinitionQuerySet":
        return self.filter(valid_from__lte=target_date).filter(
            models.Q(valid_until__isnull=True) | models.Q(valid_until__gte=target_date)
        )


class PositionDefinition(models.Model):
    name = models.CharField("Nombre", max_length=150)
    code = models.CharField("Código", max_length=64, unique=True)
    display_order = models.PositiveIntegerField("Orden de visualización", default=0, db_index=True)
    category = models.ForeignKey(
        PositionCategory,
        on_delete=models.PROTECT,
        related_name="positions",
        verbose_name="Categoría",
    )
    farm = models.ForeignKey(
        Farm,
        on_delete=models.PROTECT,
        related_name="position_definitions",
        verbose_name="Granja",
    )
    chicken_house = models.ForeignKey(
        ChickenHouse,
        on_delete=models.PROTECT,
        related_name="position_definitions",
        verbose_name="Galpón",
        null=True,
        blank=True,
    )
    rooms = models.ManyToManyField(
        Room,
        related_name="position_definitions",
        verbose_name="Salones",
        blank=True,
    )
    valid_from = models.DateField("Válido desde")
    valid_until = models.DateField("Válido hasta", null=True, blank=True)

    objects = PositionDefinitionQuerySet.as_manager()

    class Meta:
        verbose_name = "Definición de posición"
        verbose_name_plural = "Definiciones de posiciones"
        ordering = ("display_order", "id")
        db_table = "calendario_positiondefinition"

    def __str__(self) -> str:
        return f"{self.name} ({self.code})"

    def clean(self) -> None:
        super().clean()
        if self.valid_until and self.valid_until < self.valid_from:
            raise ValidationError("La fecha de fin debe ser igual o posterior a la de inicio.")

        if self.chicken_house and self.chicken_house.farm_id != self.farm_id:
            raise ValidationError("El galpón seleccionado debe pertenecer a la granja indicada.")

        if self.pk:
            room_house_ids = set(self.rooms.values_list("chicken_house_id", flat=True))
            if room_house_ids:
                if not self.chicken_house_id:
                    raise ValidationError("Debe seleccionar un galpón cuando se utilicen salones.")
                if room_house_ids != {self.chicken_house_id}:
                    raise ValidationError("Todos los salones seleccionados deben pertenecer al galpón indicado.")

    def save(self, *args, **kwargs) -> None:
        if not self.display_order:
            max_order = (
                PositionDefinition.objects.exclude(pk=self.pk)
                .aggregate(max_order=Max("display_order"))
                .get("max_order")
                or 0
            )
            self.display_order = max_order + 1
        super().save(*args, **kwargs)

    @property
    def shift_type(self) -> str:
        return self.category.shift_type

    def get_shift_type_display(self) -> str:
        try:
            return ShiftType(self.shift_type).label
        except ValueError:  # pragma: no cover - defensive
            return self.shift_type

    def is_active_on(self, target_date: date) -> bool:
        if not target_date:
            return False
        if target_date < self.valid_from:
            return False
        if self.valid_until and target_date > self.valid_until:
            return False
        return True

    def is_active_today(self) -> bool:
        return self.is_active_on(UserProfile.colombia_today())


class ShiftCalendar(models.Model):
    name = models.CharField("Nombre", max_length=150, blank=True)
    start_date = models.DateField("Fecha inicio")
    end_date = models.DateField("Fecha fin")
    status = models.CharField(
        "Estado",
        max_length=16,
        choices=CalendarStatus.choices,
        default=CalendarStatus.DRAFT,
    )
    base_calendar = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="modifications",
        verbose_name="Calendario base",
        null=True,
        blank=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="created_calendars",
        verbose_name="Creado por",
        null=True,
        blank=True,
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="approved_calendars",
        verbose_name="Aprobado por",
        null=True,
        blank=True,
    )
    approved_at = models.DateTimeField("Fecha de aprobación", null=True, blank=True)
    notes = models.TextField("Notas", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Calendario de turnos"
        verbose_name_plural = "Calendarios de turnos"
        ordering = ("-start_date", "-created_at")
        unique_together = ("start_date", "end_date", "status")
        db_table = "calendario_shiftcalendar"

    def __str__(self) -> str:
        return f"Calendario {self.start_date} - {self.end_date} ({self.get_status_display()})"

    def clean(self) -> None:
        super().clean()
        if self.end_date < self.start_date:
            raise ValidationError("La fecha final debe ser igual o posterior a la inicial.")

        if self.base_calendar and self.base_calendar_id == self.pk:
            raise ValidationError("El calendario base no puede ser el mismo calendario.")

        if (
            self.status == CalendarStatus.MODIFIED
            and not self.base_calendar
            and self._state.adding
        ):
            raise ValidationError("Un calendario modificado debe referenciar el calendario base.")

        if self.base_calendar and (
            self.start_date < timezone.localdate()
            and self.base_calendar.status == CalendarStatus.APPROVED
        ):
            # Solo permitir modificaciones para fechas futuras al día siguiente.
            raise ValidationError(
                "Las modificaciones deben iniciar en una fecha posterior al calendario aprobado."
            )

    def mark_approved(self, user: Optional[UserProfile]) -> None:
        self.status = CalendarStatus.APPROVED
        self.approved_by = user
        self.approved_at = timezone.now()
        self.save(update_fields=["status", "approved_by", "approved_at", "updated_at"])

    def create_modification(self, created_by: Optional[UserProfile]) -> "ShiftCalendar":
        calendar = ShiftCalendar.objects.create(
            name=self.name,
            start_date=self.start_date,
            end_date=self.end_date,
            status=CalendarStatus.MODIFIED,
            base_calendar=self,
            created_by=created_by,
            notes=self.notes,
        )
        return calendar


class ShiftAssignment(models.Model):
    calendar = models.ForeignKey(
        ShiftCalendar,
        on_delete=models.CASCADE,
        related_name="assignments",
        verbose_name="Calendario",
    )
    position = models.ForeignKey(
        PositionDefinition,
        on_delete=models.PROTECT,
        related_name="assignments",
        verbose_name="Posición",
    )
    date = models.DateField("Fecha")
    operator = models.ForeignKey(
        UserProfile,
        on_delete=models.PROTECT,
        related_name="shift_assignments",
        verbose_name="Operario",
    )
    is_auto_assigned = models.BooleanField("Asignación automática", default=True)
    alert_level = models.CharField(
        "Alerta",
        max_length=16,
        choices=AssignmentAlertLevel.choices,
        default=AssignmentAlertLevel.NONE,
    )
    is_overtime = models.BooleanField("Sobrecarga", default=False)
    overtime_points = models.PositiveSmallIntegerField("Puntos por sobrecarga", default=0)
    notes = models.TextField("Notas", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Asignación"
        verbose_name_plural = "Asignaciones"
        unique_together = ("calendar", "position", "date")
        ordering = ("date", "position__display_order", "position__code")
        constraints = [
            models.UniqueConstraint(
                fields=("calendar", "operator", "date"),
                name="uniq_calendar_operator_date",
            )
        ]
        db_table = "calendario_shiftassignment"

    def __str__(self) -> str:
        return f"{self.date} - {self.position} -> {self.operator}"

    def clean(self) -> None:
        super().clean()
        if not self.calendar_id:
            return

        if not (self.calendar.start_date <= self.date <= self.calendar.end_date):
            raise ValidationError("La fecha debe pertenecer al rango del calendario.")

        if not self.position.is_active_on(self.date):
            raise ValidationError("La posición no está vigente para la fecha indicada.")


class AssignmentChangeLog(models.Model):
    class ChangeType(models.TextChoices):
        CREATED = "created", _("Creada")
        UPDATED = "updated", _("Actualizada")
        DELETED = "deleted", _("Eliminada")

    assignment = models.ForeignKey(
        ShiftAssignment,
        on_delete=models.SET_NULL,
        related_name="change_logs",
        verbose_name="Asignación",
        null=True,
        blank=True,
    )
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="assignment_changes",
        verbose_name="Modificado por",
        null=True,
        blank=True,
    )
    change_type = models.CharField(
        "Tipo de cambio",
        max_length=16,
        choices=ChangeType.choices,
    )
    previous_operator = models.ForeignKey(
        UserProfile,
        on_delete=models.SET_NULL,
        related_name="previous_assignment_changes",
        null=True,
        blank=True,
    )
    new_operator = models.ForeignKey(
        UserProfile,
        on_delete=models.SET_NULL,
        related_name="new_assignment_changes",
        null=True,
        blank=True,
    )
    details = models.JSONField("Detalles", default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Historial de asignación"
        verbose_name_plural = "Historial de asignaciones"
        ordering = ("-created_at",)
        db_table = "calendario_assignmentchangelog"

    def __str__(self) -> str:
        assignment_label = str(self.assignment) if self.assignment else "Asignación eliminada"
        return f"{assignment_label} ({self.change_type})"


class WorkloadSnapshot(models.Model):
    calendar = models.ForeignKey(
        ShiftCalendar,
        on_delete=models.CASCADE,
        related_name="workload_snapshots",
    )
    operator = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name="workload_snapshots",
    )
    total_shifts = models.PositiveIntegerField("Turnos asignados", default=0)
    day_shifts = models.PositiveIntegerField("Turnos diurnos", default=0)
    night_shifts = models.PositiveIntegerField("Turnos nocturnos", default=0)
    rest_days = models.PositiveIntegerField("Descansos", default=0)
    overtime_days = models.PositiveIntegerField("Días extra", default=0)
    overtime_points_total = models.PositiveIntegerField("Puntos por sobrecarga", default=0)
    month_reference = models.DateField("Mes de referencia")

    class Meta:
        verbose_name = "Carga de trabajo"
        verbose_name_plural = "Cargas de trabajo"
        unique_together = ("calendar", "operator", "month_reference")
        db_table = "calendario_workloadsnapshot"

    def __str__(self) -> str:
        return f"Carga {self.operator} ({self.month_reference})"


class OperatorRestPeriod(models.Model):
    operator = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name="rest_periods",
        verbose_name="Operario",
    )
    start_date = models.DateField("Inicio")
    end_date = models.DateField("Fin")
    status = models.CharField(
        "Estado",
        max_length=16,
        choices=RestPeriodStatus.choices,
        default=RestPeriodStatus.PLANNED,
    )
    source = models.CharField(
        "Origen",
        max_length=16,
        choices=RestPeriodSource.choices,
        default=RestPeriodSource.MANUAL,
    )
    calendar = models.ForeignKey(
        "ShiftCalendar",
        on_delete=models.SET_NULL,
        related_name="rest_periods",
        verbose_name="Calendario origen",
        null=True,
        blank=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="created_rest_periods",
        verbose_name="Creado por",
        null=True,
        blank=True,
    )
    notes = models.TextField("Notas", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Periodo de descanso"
        verbose_name_plural = "Periodos de descanso"
        ordering = ("-start_date", "-created_at")
        indexes = [
            models.Index(fields=("operator", "start_date")),
            models.Index(fields=("operator", "end_date")),
        ]
        db_table = "calendario_operatorrestperiod"

    def __str__(self) -> str:
        return f"{self.operator} · {self.start_date} → {self.end_date} ({self.get_status_display()})"

    def clean(self) -> None:
        super().clean()
        if self.end_date < self.start_date:
            raise ValidationError("La fecha final del descanso debe ser posterior o igual al inicio.")

    def save(self, *args, **kwargs) -> None:
        self.full_clean()
        super().save(*args, **kwargs)


@dataclass
class AssignmentDecision:
    position: PositionDefinition
    operator: Optional[UserProfile]
    date: date
    alert_level: AssignmentAlertLevel = AssignmentAlertLevel.NONE
    is_overtime: bool = False
    notes: str = ""
    overtime_points: int = 0


@dataclass(frozen=True)
class OverloadPolicyData:
    extra_day_limit: int = 0
    overtime_points: int = 0
    alert_level: AssignmentAlertLevel = AssignmentAlertLevel.NONE


def resolve_overload_policy(category: PositionCategory) -> OverloadPolicyData:
    shift_type = category.shift_type
    limit_cap = 2 if shift_type == ShiftType.NIGHT else 3
    extra_limit = max(limit_cap, 1)
    return OverloadPolicyData(extra_day_limit=extra_limit)

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Max
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from granjas.models import ChickenHouse, Farm, Room
from users.models import UserProfile


class ShiftType(models.TextChoices):
    DAY = "day", _("Día")
    NIGHT = "night", _("Noche")
    MIXED = "mixed", _("Mixto")


class AssignmentAlertLevel(models.TextChoices):
    NONE = "none", _("Sin alerta")
    WARN = "warn", _("Desajuste moderado")
    CRITICAL = "critical", _("Desajuste crítico")


class ComplexityLevel(models.TextChoices):
    BASIC = "basic", _("Manejable")
    INTERMEDIATE = "intermediate", _("Importante")
    ADVANCED = "advanced", _("Crítico")


COMPLEXITY_LEVEL_SCORE = {
    ComplexityLevel.BASIC: 1,
    ComplexityLevel.INTERMEDIATE: 2,
    ComplexityLevel.ADVANCED: 3,
}


def complexity_score(level: str) -> int:
    try:
        enumeration = ComplexityLevel(level)
    except ValueError as exc:  # pragma: no cover - defensive
        raise ValidationError(f"Nivel de criticidad inválido: {level}") from exc
    return COMPLEXITY_LEVEL_SCORE[enumeration]


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
    name = models.CharField("Nombre", max_length=150)
    shift_type = models.CharField(
        "Turno",
        max_length=16,
        choices=ShiftType.choices,
        default=ShiftType.DAY,
    )
    extra_day_limit = models.PositiveSmallIntegerField(
        "Máximo días extra consecutivos",
        validators=[MinValueValidator(1)],
        default=3,
    )
    overtime_points = models.PositiveSmallIntegerField(
        "Puntos por día extra",
        validators=[MinValueValidator(1)],
        default=1,
    )
    overload_alert_level = models.CharField(
        "Nivel de alerta de sobrecarga",
        max_length=16,
        choices=AssignmentAlertLevel.choices,
        default=AssignmentAlertLevel.WARN,
    )
    rest_min_frequency = models.PositiveSmallIntegerField(
        "Frecuencia mínima de descanso",
        validators=[MinValueValidator(1)],
        default=6,
    )
    rest_min_consecutive_days = models.PositiveSmallIntegerField(
        "Días de descanso consecutivos mínimos",
        validators=[MinValueValidator(1)],
        default=5,
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
        ordering = ("name", "code")

    def __str__(self) -> str:
        return self.name

    @property
    def is_night_shift(self) -> bool:
        return self.shift_type == ShiftType.NIGHT


class DayOfWeek(models.IntegerChoices):
    MONDAY = 0, _("Lunes")
    TUESDAY = 1, _("Martes")
    WEDNESDAY = 2, _("Miércoles")
    THURSDAY = 3, _("Jueves")
    FRIDAY = 4, _("Viernes")
    SATURDAY = 5, _("Sábado")
    SUNDAY = 6, _("Domingo")


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
    complexity = models.CharField(
        "Nivel de criticidad",
        max_length=16,
        choices=ComplexityLevel.choices,
        default=ComplexityLevel.BASIC,
    )
    allow_lower_complexity = models.BooleanField(
        "Permitir cubrir con criticidad inferior", default=False
    )
    valid_from = models.DateField("Válido desde")
    valid_until = models.DateField("Válido hasta", null=True, blank=True)
    is_active = models.BooleanField("Activo", default=True)
    notes = models.TextField("Notas", blank=True)

    objects = PositionDefinitionQuerySet.as_manager()

    class Meta:
        verbose_name = "Definición de posición"
        verbose_name_plural = "Definiciones de posiciones"
        ordering = ("display_order", "id")

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
        if not self.is_active:
            return False
        if target_date < self.valid_from:
            return False
        if self.valid_until and target_date > self.valid_until:
            return False
        return True


COMPLEXITY_SKILL_THRESHOLDS: dict[str, tuple[int, int]] = {
    ComplexityLevel.BASIC: (1, 3),
    ComplexityLevel.INTERMEDIATE: (3, 7),
    ComplexityLevel.ADVANCED: (8, 10),
}


def required_skill_for_complexity(level: str) -> int:
    try:
        enumeration = ComplexityLevel(level)
    except ValueError as exc:  # pragma: no cover - defensive
        raise ValidationError(f"Nivel de criticidad inválido: {level}") from exc
    minimum, _maximum = COMPLEXITY_SKILL_THRESHOLDS[enumeration]
    return minimum


class OperatorCapability(models.Model):
    operator = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name="capabilities",
        verbose_name="Operario",
    )
    category = models.ForeignKey(
        PositionCategory,
        on_delete=models.CASCADE,
        related_name="capabilities",
        verbose_name="Categoría",
    )
    skill_score = models.PositiveSmallIntegerField(
        "Nivel de habilidad",
        validators=[MinValueValidator(1), MaxValueValidator(10)],
        default=5,
    )

    class Meta:
        verbose_name = "Capacidad de operario"
        verbose_name_plural = "Capacidades de operarios"
        unique_together = ("operator", "category")
        ordering = (
            "operator__apellidos",
            "operator__nombres",
            "category__name",
        )

    def __str__(self) -> str:
        return f"{self.operator} - {self.category.name} ({self.skill_score}/10)"


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
    extra_day_limit: int
    overtime_points: int
    alert_level: AssignmentAlertLevel


def filter_capabilities_for_category(
    capabilities: Iterable[OperatorCapability],
    category_id: int,
) -> list[OperatorCapability]:
    return [capability for capability in capabilities if capability.category_id == category_id]


def resolve_overload_policy(category: PositionCategory) -> OverloadPolicyData:
    shift_type = category.shift_type
    limit_cap = 2 if shift_type == ShiftType.NIGHT else 3
    extra_limit = min(category.extra_day_limit, limit_cap)
    extra_limit = max(extra_limit, 1)
    return OverloadPolicyData(
        extra_day_limit=extra_limit,
        overtime_points=category.overtime_points,
        alert_level=AssignmentAlertLevel(category.overload_alert_level),
    )

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from granjas.models import ChickenHouse, Farm, Room
from users.models import Role, UserProfile


class ShiftType(models.TextChoices):
    DAY = "day", _("Día")
    NIGHT = "night", _("Noche")
    MIXED = "mixed", _("Mixto")


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


class PositionCategory(models.TextChoices):
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


CATEGORY_SHIFT_MAP: dict[str, str] = {
    PositionCategory.GALPONERO_PRODUCCION_DIA: ShiftType.DAY,
    PositionCategory.GALPONERO_LEVANTE_DIA: ShiftType.DAY,
    PositionCategory.GALPONERO_PRODUCCION_NOCHE: ShiftType.NIGHT,
    PositionCategory.GALPONERO_LEVANTE_NOCHE: ShiftType.NIGHT,
    PositionCategory.CLASIFICADOR_DIA: ShiftType.DAY,
    PositionCategory.CLASIFICADOR_NOCHE: ShiftType.NIGHT,
    PositionCategory.LIDER_GRANJA: ShiftType.MIXED,
    PositionCategory.SUPERVISOR: ShiftType.DAY,
    PositionCategory.LIDER_TECNICO: ShiftType.DAY,
    PositionCategory.OFICIOS_VARIOS: ShiftType.DAY,
}


def shift_type_for_category(category: str) -> str:
    return CATEGORY_SHIFT_MAP.get(category, ShiftType.DAY)


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


class AssignmentAlertLevel(models.TextChoices):
    NONE = "none", _("Sin alerta")
    WARN = "warn", _("Desajuste moderado")
    CRITICAL = "critical", _("Desajuste crítico")


class PositionDefinitionQuerySet(models.QuerySet):
    def active_on(self, target_date: date) -> "PositionDefinitionQuerySet":
        return self.filter(valid_from__lte=target_date).filter(
            models.Q(valid_until__isnull=True) | models.Q(valid_until__gte=target_date)
        )


class PositionDefinition(models.Model):
    name = models.CharField("Nombre", max_length=150)
    code = models.CharField("Código", max_length=64, unique=True)
    category = models.CharField(
        "Categoría",
        max_length=64,
        choices=PositionCategory.choices,
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
        ordering = ("farm__name", "code")

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

    @property
    def shift_type(self) -> str:
        return shift_type_for_category(self.category)

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


class OperatorCapability(models.Model):
    operator = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name="capabilities",
        verbose_name="Operario",
    )
    category = models.CharField(
        "Categoría",
        max_length=64,
        choices=PositionCategory.choices,
    )
    max_complexity = models.CharField(
        "Criticidad máxima manejada",
        max_length=16,
        choices=ComplexityLevel.choices,
        default=ComplexityLevel.BASIC,
    )
    min_complexity = models.CharField(
        "Criticidad mínima",
        max_length=16,
        choices=ComplexityLevel.choices,
        default=ComplexityLevel.BASIC,
    )
    effective_from = models.DateField("Válido desde", default=timezone.localdate)
    effective_until = models.DateField("Válido hasta", null=True, blank=True)
    is_primary = models.BooleanField("Principal", default=True)
    notes = models.TextField("Notas", blank=True)

    class Meta:
        verbose_name = "Capacidad de operario"
        verbose_name_plural = "Capacidades de operarios"
        unique_together = (
            "operator",
            "category",
            "effective_from",
        )
        ordering = (
            "operator__apellidos",
            "operator__nombres",
            "category",
        )

    def __str__(self) -> str:
        return f"{self.operator} - {self.category}"

    def clean(self) -> None:
        super().clean()
        if self.effective_until and self.effective_until < self.effective_from:
            raise ValidationError("La fecha de fin debe ser igual o posterior a la de inicio.")

        if complexity_score(self.min_complexity) > complexity_score(self.max_complexity):
            raise ValidationError("La criticidad mínima no puede ser mayor que la máxima.")

    def is_active_on(self, target_date: date) -> bool:
        if target_date < self.effective_from:
            return False
        if self.effective_until and target_date > self.effective_until:
            return False
        return True


class OperatorFarmPreference(models.Model):
    operator = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name="farm_preferences",
        verbose_name="Operario",
    )
    farm = models.ForeignKey(
        Farm,
        on_delete=models.CASCADE,
        related_name="operator_preferences",
        verbose_name="Granja",
    )
    preference_weight = models.PositiveSmallIntegerField(
        "Nivel de preferencia", validators=[MinValueValidator(1)], default=1
    )
    is_primary = models.BooleanField("Preferencia principal", default=False)
    notes = models.TextField("Notas", blank=True)

    class Meta:
        verbose_name = "Preferencia de granja"
        verbose_name_plural = "Preferencias de granja"
        unique_together = ("operator", "farm")
        ordering = (
            "operator__apellidos",
            "operator__nombres",
            "preference_weight",
        )

    def __str__(self) -> str:
        return f"{self.operator} → {self.farm}"


class RestRule(models.Model):
    role = models.ForeignKey(
        Role,
        on_delete=models.CASCADE,
        related_name="rest_rules",
        verbose_name="Rol",
    )
    shift_type = models.CharField(
        "Turno",
        max_length=16,
        choices=ShiftType.choices,
        default=ShiftType.DAY,
    )
    min_rest_frequency = models.PositiveSmallIntegerField(
        "Descanso mínimo cada X días", validators=[MinValueValidator(1)], default=6
    )
    min_consecutive_days = models.PositiveSmallIntegerField(
        "Mínimo días consecutivos", validators=[MinValueValidator(1)], default=5
    )
    max_consecutive_days = models.PositiveSmallIntegerField(
        "Máximo días consecutivos", validators=[MinValueValidator(1)], default=8
    )
    post_shift_rest_days = models.PositiveSmallIntegerField(
        "Posturno (días)", default=0
    )
    monthly_rest_days = models.PositiveSmallIntegerField(
        "Descansos mensuales", validators=[MinValueValidator(1)], default=5
    )
    enforce_additional_rest = models.BooleanField("Descanso adicional estricto", default=False)
    active_from = models.DateField("Vigente desde", default=timezone.localdate)
    active_until = models.DateField("Vigente hasta", null=True, blank=True)

    class Meta:
        verbose_name = "Regla de descanso"
        verbose_name_plural = "Reglas de descanso"
        unique_together = ("role", "shift_type", "active_from")
        ordering = (
            "role__name",
            "shift_type",
            "-active_from",
        )

    def __str__(self) -> str:
        return f"{self.role} ({self.get_shift_type_display()})"

    def clean(self) -> None:
        super().clean()
        if self.active_until and self.active_until < self.active_from:
            raise ValidationError("La vigencia final debe ser posterior al inicio.")

        if self.min_consecutive_days > self.max_consecutive_days:
            raise ValidationError(
                "El mínimo de días consecutivos no puede ser mayor al máximo permitido."
            )


class RestPreference(models.Model):
    rest_rule = models.ForeignKey(
        RestRule,
        on_delete=models.CASCADE,
        related_name="preferred_days",
        verbose_name="Regla",
    )
    day_of_week = models.IntegerField(
        "Día de la semana",
        choices=DayOfWeek.choices,
    )
    is_required = models.BooleanField("Obligatorio", default=False)

    class Meta:
        verbose_name = "Preferencia de descanso"
        verbose_name_plural = "Preferencias de descanso"
        unique_together = ("rest_rule", "day_of_week")
        ordering = ("rest_rule", "day_of_week")

    def __str__(self) -> str:
        return f"{self.rest_rule} - {self.get_day_of_week_display()}"


class OverloadAllowance(models.Model):
    role = models.ForeignKey(
        Role,
        on_delete=models.CASCADE,
        related_name="overload_rules",
        verbose_name="Rol",
    )
    max_consecutive_extra_days = models.PositiveSmallIntegerField(
        "Máximo días extra", validators=[MinValueValidator(1)], default=3
    )
    highlight_level = models.CharField(
        "Nivel de resaltado",
        max_length=16,
        choices=AssignmentAlertLevel.choices,
        default=AssignmentAlertLevel.WARN,
    )
    active_from = models.DateField("Vigente desde", default=timezone.localdate)
    active_until = models.DateField("Vigente hasta", null=True, blank=True)

    class Meta:
        verbose_name = "Regla de sobrecarga"
        verbose_name_plural = "Reglas de sobrecarga"
        unique_together = ("role", "active_from")
        ordering = ("role__name", "-active_from")

    def __str__(self) -> str:
        return f"Sobrecarga {self.role}"

    def clean(self) -> None:
        super().clean()
        if self.active_until and self.active_until < self.active_from:
            raise ValidationError("La vigencia final debe ser posterior al inicio.")


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

        if self.status == CalendarStatus.MODIFIED and not self.base_calendar:
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
    notes = models.TextField("Notas", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Asignación"
        verbose_name_plural = "Asignaciones"
        unique_together = ("calendar", "position", "date")
        ordering = ("date", "position__code")

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
    month_reference = models.DateField("Mes de referencia")

    class Meta:
        verbose_name = "Carga de trabajo"
        verbose_name_plural = "Cargas de trabajo"
        unique_together = ("calendar", "operator", "month_reference")

    def __str__(self) -> str:
        return f"Carga {self.operator} ({self.month_reference})"


@dataclass
class AssignmentDecision:
    position: PositionDefinition
    operator: Optional[UserProfile]
    date: date
    alert_level: AssignmentAlertLevel = AssignmentAlertLevel.NONE
    is_overtime: bool = False
    notes: str = ""


def filter_capabilities_for_category(
    capabilities: Iterable[OperatorCapability],
    category: str,
    target_date: date,
) -> list[OperatorCapability]:
    return [
        capability
        for capability in capabilities
        if capability.category == category and capability.is_active_on(target_date)
    ]

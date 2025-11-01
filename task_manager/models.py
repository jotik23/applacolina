from __future__ import annotations

from functools import lru_cache
from typing import Iterable, Optional

from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import BooleanField, Case, F, IntegerField, Q, Value, When
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from production.models import ChickenHouse, Farm, Room
from personal.models import DayOfWeek, PositionDefinition, UserProfile
from production.models import ProductionRecord


class TaskStatus(models.Model):
    name = models.CharField(_("Nombre"), max_length=80, unique=True)
    is_active = models.BooleanField(_("Activo"), default=True)

    OVERDUE_NAME: str = "Vencido"

    class Meta:
        verbose_name = _("Estado de tarea")
        verbose_name_plural = _("Estados de tareas")
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name

    @classmethod
    @lru_cache(maxsize=1)
    def overdue_status(cls) -> Optional["TaskStatus"]:
        try:
            return cls.objects.get(name__iexact=cls.OVERDUE_NAME)
        except cls.DoesNotExist:
            return None

    @classmethod
    def overdue_status_id(cls) -> Optional[int]:
        status = cls.overdue_status()
        return status.pk if status else None


class TaskCategory(models.Model):
    name = models.CharField(_("Nombre"), max_length=80, unique=True)
    description = models.TextField(_("Descripción"), blank=True)
    is_active = models.BooleanField(_("Activo"), default=True)

    class Meta:
        verbose_name = _("Categoría de tarea")
        verbose_name_plural = _("Categorías de tareas")
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name


class TaskDefinitionQuerySet(models.QuerySet):
    def with_overdue_state(self) -> "TaskDefinitionQuerySet":
        today = timezone.localdate()
        overdue_condition = Q(
            task_type="one_time",
            scheduled_for__isnull=False,
            scheduled_for__lt=today,
        )
        overdue_status_id = TaskStatus.overdue_status_id()
        annotations: dict[str, models.Expression] = {
            "is_overdue": Case(
                When(overdue_condition, then=Value(True)),
                default=Value(False),
                output_field=BooleanField(),
            ),
        }
        if overdue_status_id is not None:
            annotations["effective_status_id"] = Case(
                When(overdue_condition, then=Value(overdue_status_id)),
                default=F("status_id"),
                output_field=IntegerField(),
            )
        else:
            annotations["effective_status_id"] = F("status_id")
        return self.annotate(**annotations)


class TaskDefinitionManager(models.Manager.from_queryset(TaskDefinitionQuerySet)):
    def get_queryset(self) -> TaskDefinitionQuerySet:
        queryset = super().get_queryset()
        return queryset.with_overdue_state()


class TaskDefinition(models.Model):
    class TaskType(models.TextChoices):
        ONE_TIME = "one_time", _("Única")
        RECURRING = "recurring", _("Recurrente")

    class EvidenceRequirement(models.TextChoices):
        NONE = "none", _("Sin evidencia")
        PHOTO = "photo", _("Fotografía obligatoria")
        VIDEO = "video", _("Video obligatorio")
        PHOTO_OR_VIDEO = "photo_or_video", _("Foto o video obligatorio")

    class RecordFormat(models.TextChoices):
        NONE = "none", _("No requiere formato")
        PRODUCTION_RECORD = "production_record", _("Registro de producción")

    RECURRENCE_ARRAY_FIELDS: tuple[str, ...] = (
        "weekly_days",
        "fortnight_days",
        "month_days",
        "monthly_week_days",
    )

    name = models.CharField(_("Tarea"), max_length=200)
    description = models.TextField(_("Descripción"), blank=True)
    display_order = models.PositiveIntegerField(
        _("Orden de visualización"),
        default=0,
        editable=False,
        db_index=True,
    )
    status = models.ForeignKey(
        TaskStatus,
        on_delete=models.PROTECT,
        related_name="task_definitions",
        verbose_name=_("Estado"),
    )
    category = models.ForeignKey(
        TaskCategory,
        on_delete=models.PROTECT,
        related_name="task_definitions",
        verbose_name=_("Categoría"),
    )
    task_type = models.CharField(
        _("Tipo"),
        max_length=16,
        choices=TaskType.choices,
        blank=True,
        null=True,
        default=None,
    )
    scheduled_for = models.DateField(
        _("Fecha programada"),
        null=True,
        blank=True,
        help_text=_("Requerido solo cuando la tarea es de ejecución única."),
    )
    weekly_days = ArrayField(
        models.PositiveSmallIntegerField(choices=DayOfWeek.choices),
        default=list,
        blank=True,
        verbose_name=_("Días de la semana"),
        help_text=_("Seleccione los días de la semana para tareas recurrentes semanales."),
    )
    fortnight_days = ArrayField(
        models.PositiveSmallIntegerField(),
        default=list,
        blank=True,
        verbose_name=_("Días de quincena"),
        help_text=_(
            "Seleccione los días específicos dentro de la quincena (1-31) cuando aplique."
        ),
    )
    monthly_week_days = ArrayField(
        models.PositiveSmallIntegerField(),
        default=list,
        blank=True,
        verbose_name=_("Semanas del mes"),
        help_text=_(
            "Defina la semana del mes en la que se debe ejecutar (1 a 5) cuando aplique."
        ),
    )
    month_days = ArrayField(
        models.PositiveSmallIntegerField(
            validators=[MinValueValidator(1), MaxValueValidator(31)]
        ),
        default=list,
        blank=True,
        verbose_name=_("Días del mes"),
        help_text=_(
            "Seleccione los días del mes (1-31) para tareas recurrentes quincenales o mensuales."
        ),
    )
    position = models.ForeignKey(
        PositionDefinition,
        on_delete=models.PROTECT,
        related_name="task_definitions",
        verbose_name=_("Posición asignada"),
        null=True,
        blank=True,
    )
    collaborator = models.ForeignKey(
        UserProfile,
        on_delete=models.PROTECT,
        related_name="task_definitions",
        verbose_name=_("Colaborador asignado"),
        null=True,
        blank=True,
    )
    evidence_requirement = models.CharField(
        _("Requisito de evidencia"),
        max_length=20,
        choices=EvidenceRequirement.choices,
        default=EvidenceRequirement.NONE,
        help_text=_(
            "Define si la tarea exige evidencia multimedia (foto o video) al finalizar."
        ),
    )
    record_format = models.CharField(
        _("Formato de registro"),
        max_length=32,
        choices=RecordFormat.choices,
        default=RecordFormat.NONE,
        help_text=_("Selecciona el formato de registro obligatorio para cerrar la tarea."),
    )
    farms = models.ManyToManyField(
        Farm,
        related_name="task_definitions",
        verbose_name=_("Granjas"),
        blank=True,
    )
    chicken_houses = models.ManyToManyField(
        ChickenHouse,
        related_name="task_definitions",
        verbose_name=_("Galpones"),
        blank=True,
    )
    rooms = models.ManyToManyField(
        Room,
        related_name="task_definitions",
        verbose_name=_("Salones"),
        blank=True,
    )
    created_at = models.DateTimeField(_("Creado en"), auto_now_add=True)
    updated_at = models.DateTimeField(_("Actualizado en"), auto_now=True)

    objects = TaskDefinitionManager()

    class Meta:
        verbose_name = _("Tarea")
        verbose_name_plural = _("Tareas")
        ordering = ("display_order", "name", "pk")

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs) -> None:
        assign_order = self.display_order is None or self.display_order <= 0
        if assign_order:
            manager = type(self).objects
            if self.pk:
                manager = manager.exclude(pk=self.pk)
            next_order = manager.aggregate(models.Max("display_order")).get("display_order__max") or 0
            self.display_order = next_order + 1
        super().save(*args, **kwargs)

    def clean(self) -> None:
        super().clean()
        self._normalize_recurrence_arrays()
        self._validate_schedule_configuration()

    def _normalize_recurrence_arrays(self) -> None:
        for field_name in self.RECURRENCE_ARRAY_FIELDS:
            values = getattr(self, field_name) or []
            sanitized_values: list[int] = []
            for value in values:
                if value is None:
                    continue
                sanitized_values.append(int(value))
            setattr(self, field_name, sorted(set(sanitized_values)))

    def _validate_schedule_configuration(self) -> None:
        if not self.task_type:
            self.task_type = None
            self.scheduled_for = self.scheduled_for or None
            self._clear_recurrence_arrays()
            return

        if self.task_type == self.TaskType.ONE_TIME:
            if not self.scheduled_for:
                raise ValidationError({"scheduled_for": _("Debe establecer la fecha programada.")})
            has_recurrence_values = any(
                getattr(self, field_name) for field_name in self.RECURRENCE_ARRAY_FIELDS
            )
            self._clear_recurrence_arrays()
            if has_recurrence_values:
                raise ValidationError(
                    _("Las configuraciones recurrentes solo aplican a tareas recurrentes.")
                )
            return

        if self.task_type == self.TaskType.RECURRING:
            recurrence_fields: list[tuple[str, Iterable[int]]] = [
                (field_name, getattr(self, field_name) or [])
                for field_name in self.RECURRENCE_ARRAY_FIELDS
            ]

            if not any(values for _, values in recurrence_fields):
                raise ValidationError(
                    _("Debe definir al menos una configuración de recurrencia para la tarea.")
                )

            if self.scheduled_for:
                raise ValidationError(
                    {"scheduled_for": _("Las tareas recurrentes no deben tener fecha puntual.")}
                )
            return

    def _clear_recurrence_arrays(self) -> None:
        for field_name in self.RECURRENCE_ARRAY_FIELDS:
            setattr(self, field_name, [])

    def _compute_is_overdue(self, reference_date=None) -> bool:
        if reference_date is None:
            reference_date = timezone.localdate()
        if self.task_type != self.TaskType.ONE_TIME:
            return False
        if not self.scheduled_for:
            return False
        return self.scheduled_for < reference_date

    @property
    def is_overdue(self) -> bool:
        cached = getattr(self, "_is_overdue_cache", None)
        if cached is not None:
            return bool(cached)
        computed = self._compute_is_overdue()
        self._is_overdue_cache = computed
        return computed

    @is_overdue.setter
    def is_overdue(self, value: bool) -> None:
        self._is_overdue_cache = bool(value)

    def _compute_effective_status_id(self) -> Optional[int]:
        overdue_status_id = TaskStatus.overdue_status_id()
        if self.is_overdue and overdue_status_id is not None:
            return overdue_status_id
        return self.status_id

    @property
    def effective_status_id(self) -> Optional[int]:
        cached = getattr(self, "_effective_status_id_cache", None)
        if cached is not None:
            return cached
        computed = self._compute_effective_status_id()
        self._effective_status_id_cache = computed
        return computed

    @effective_status_id.setter
    def effective_status_id(self, value: Optional[int]) -> None:
        self._effective_status_id_cache = None if value is None else int(value)

    @property
    def effective_status(self) -> Optional[TaskStatus]:
        if self.is_overdue:
            overdue_status = TaskStatus.overdue_status()
            if overdue_status is not None:
                return overdue_status
        return self.status


class TaskAssignment(models.Model):
    task_definition = models.ForeignKey(
        TaskDefinition,
        on_delete=models.PROTECT,
        related_name="assignments",
        verbose_name=_("Tarea"),
    )
    collaborator = models.ForeignKey(
        UserProfile,
        on_delete=models.PROTECT,
        related_name="task_assignments",
        verbose_name=_("Colaborador"),
    )
    due_date = models.DateField(_("Fecha programada"))
    completed_on = models.DateField(
        _("Fecha de finalización"),
        null=True,
        blank=True,
    )
    production_record = models.OneToOneField(
        ProductionRecord,
        on_delete=models.SET_NULL,
        related_name="task_assignment",
        verbose_name=_("Registro de producción"),
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(_("Creado en"), auto_now_add=True)
    updated_at = models.DateTimeField(_("Actualizado en"), auto_now=True)

    class Meta:
        verbose_name = _("Asignación de tarea")
        verbose_name_plural = _("Asignaciones de tareas")
        ordering = ("due_date", "task_definition__name")
        unique_together = ("task_definition", "collaborator", "due_date")

    def __str__(self) -> str:
        return f"{self.task_definition} · {self.collaborator} · {self.due_date:%Y-%m-%d}"

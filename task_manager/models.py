from __future__ import annotations

from typing import Iterable

from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from granjas.models import ChickenHouse, Farm, Room
from personal.models import DayOfWeek, PositionDefinition, UserProfile
from production.models import ProductionRecord


class TaskStatus(models.Model):
    name = models.CharField(_("Nombre"), max_length=80, unique=True)
    is_active = models.BooleanField(_("Activo"), default=True)

    class Meta:
        verbose_name = _("Estado de tarea")
        verbose_name_plural = _("Estados de tareas")
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name


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


class TaskDefinition(models.Model):
    class TaskType(models.TextChoices):
        ONE_TIME = "one_time", _("Única")
        RECURRING = "recurring", _("Recurrente")

    name = models.CharField(_("Tarea"), max_length=200)
    description = models.TextField(_("Descripción"), blank=True)
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
        default=TaskType.ONE_TIME,
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

    class Meta:
        verbose_name = _("Definición de tarea")
        verbose_name_plural = _("Definiciones de tareas")
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        super().clean()
        self._validate_schedule_configuration()

    def _validate_schedule_configuration(self) -> None:
        if self.task_type == self.TaskType.ONE_TIME:
            if not self.scheduled_for:
                raise ValidationError({"scheduled_for": _("Debe establecer la fecha programada.")})
            if self.weekly_days or self.month_days:
                raise ValidationError(
                    _("Las configuraciones recurrentes solo aplican a tareas recurrentes.")
                )
            return

        if self.task_type == self.TaskType.RECURRING:
            recurrence_fields: list[tuple[str, Iterable[int]]] = [
                ("weekly_days", self.weekly_days or []),
                ("month_days", self.month_days or []),
            ]

            if not any(values for _, values in recurrence_fields):
                raise ValidationError(
                    _("Debe definir al menos una configuración de recurrencia para la tarea.")
                )

            if self.scheduled_for:
                raise ValidationError(
                    {"scheduled_for": _("Las tareas recurrentes no deben tener fecha puntual.")}
                )


class TaskAssignment(models.Model):
    task_definition = models.ForeignKey(
        TaskDefinition,
        on_delete=models.PROTECT,
        related_name="assignments",
        verbose_name=_("Definición de tarea"),
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

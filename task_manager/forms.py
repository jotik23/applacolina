from __future__ import annotations

from typing import Iterable

from django import forms
from django.utils.translation import gettext_lazy as _

from personal.models import DayOfWeek, PositionDefinition, UserProfile
from production.models import ChickenHouse, Farm, Room

from .models import TaskCategory, TaskDefinition, TaskStatus


FIELD_INPUT_CLASSES = (
    "mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700 "
    "focus:border-brand focus:outline-none focus:ring-2 focus:ring-brand/20"
)
TEXTAREA_CLASSES = FIELD_INPUT_CLASSES + " min-h-[120px] resize-y"
MULTISELECT_CLASSES = FIELD_INPUT_CLASSES + " h-36"


class TaskDefinitionQuickCreateForm(forms.ModelForm):
    weekly_days = forms.TypedMultipleChoiceField(
        label=_("Días de la semana"),
        choices=DayOfWeek.choices,
        required=False,
        coerce=int,
        widget=forms.SelectMultiple(
            attrs={
                "class": MULTISELECT_CLASSES,
                "data-field-name": "weekly_days",
            }
        ),
        help_text=_("Selecciona los días de la semana en los que aplica la tarea."),
    )
    month_days = forms.TypedMultipleChoiceField(
        label=_("Días del mes"),
        choices=[(day, day) for day in range(1, 32)],
        required=False,
        coerce=int,
        widget=forms.SelectMultiple(
            attrs={
                "class": MULTISELECT_CLASSES,
                "data-field-name": "month_days",
            }
        ),
        help_text=_("Útil para tareas quincenales o mensuales. Selecciona uno o varios días del mes."),
    )

    class Meta:
        model = TaskDefinition
        fields = [
            "name",
            "description",
            "status",
            "category",
            "task_type",
            "scheduled_for",
            "weekly_days",
            "month_days",
            "position",
            "collaborator",
            "farms",
            "chicken_houses",
            "rooms",
            "evidence_requirement",
            "record_format",
        ]
        widgets = {
            "name": forms.TextInput(
                attrs={
                    "class": FIELD_INPUT_CLASSES,
                    "placeholder": _("Ej. Sanitizar equipos de pesaje"),
                }
            ),
            "description": forms.Textarea(
                attrs={
                    "rows": 3,
                    "class": TEXTAREA_CLASSES,
                    "placeholder": _(
                        "Detalla la rutina, estándares y protocolos a cumplir."
                    ),
                }
            ),
            "status": forms.Select(attrs={"class": FIELD_INPUT_CLASSES}),
            "category": forms.Select(attrs={"class": FIELD_INPUT_CLASSES}),
            "task_type": forms.Select(
                attrs={
                    "class": FIELD_INPUT_CLASSES,
                    "data-field-name": "task_type",
                }
            ),
            "scheduled_for": forms.DateInput(
                attrs={
                    "type": "date",
                    "class": FIELD_INPUT_CLASSES,
                }
            ),
            "position": forms.Select(
                attrs={
                    "class": FIELD_INPUT_CLASSES,
                }
            ),
            "collaborator": forms.Select(
                attrs={
                    "class": FIELD_INPUT_CLASSES,
                }
            ),
            "farms": forms.SelectMultiple(attrs={"class": MULTISELECT_CLASSES}),
            "chicken_houses": forms.SelectMultiple(attrs={"class": MULTISELECT_CLASSES}),
            "rooms": forms.SelectMultiple(attrs={"class": MULTISELECT_CLASSES}),
            "evidence_requirement": forms.Select(attrs={"class": FIELD_INPUT_CLASSES}),
            "record_format": forms.Select(attrs={"class": FIELD_INPUT_CLASSES}),
        }
        labels = {
            "name": _("Nombre de la tarea"),
            "description": _("Descripción"),
            "status": _("Estado"),
            "category": _("Categoría"),
            "task_type": _("Recurrencia"),
            "scheduled_for": _("Fecha puntual"),
            "position": _("Posición prioritaria"),
            "collaborator": _("Colaborador sugerido"),
            "farms": _("Granjas"),
            "chicken_houses": _("Galpones"),
            "rooms": _("Salones"),
            "evidence_requirement": _("Evidencia multimedia"),
            "record_format": _("Formato de registro"),
        }
        help_texts = {
            "scheduled_for": _(
                "Obligatorio únicamente cuando la tarea es de ejecución única."
            ),
            "position": _(
                "El generador la utilizará como posición base para las asignaciones."
            ),
            "collaborator": _(
                "Selecciona un colaborador sugerido opcional; se validará su vigencia."
            ),
            "farms": _("Limita la tarea a una o varias granjas específicas."),
            "chicken_houses": _("Filtra por galpones concretos dentro de las granjas."),
            "rooms": _("Puedes asociar salones específicos si aplica."),
            "evidence_requirement": _(
                "Define si se debe cargar una foto o video para cerrar la tarea."
            ),
            "record_format": _(
                "Selecciona el formato de registro que se debe diligenciar al completar la tarea."
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._configure_choice_fields()
        self._assign_css_classes()
        task_type_field = self.fields.get("task_type")
        if task_type_field:
            task_type_field.required = False
            task_type_field.initial = None
            task_type_field.choices = [("", _("Sin recurrencia"))] + list(
                TaskDefinition.TaskType.choices
            )

    def _configure_choice_fields(self) -> None:
        self.fields["status"].queryset = TaskStatus.objects.filter(is_active=True).order_by(
            "name"
        )
        self.fields["category"].queryset = (
            TaskCategory.objects.filter(is_active=True).order_by("name")
        )
        self.fields["position"].queryset = (
            PositionDefinition.objects.select_related("farm", "chicken_house")
            .order_by("display_order", "name")
        )
        self.fields["collaborator"].queryset = (
            UserProfile.objects.filter(is_active=True).order_by("apellidos", "nombres")
        )
        self.fields["farms"].queryset = Farm.objects.order_by("name")
        self.fields["chicken_houses"].queryset = ChickenHouse.objects.select_related("farm").order_by(
            "farm__name", "name"
        )
        self.fields["rooms"].queryset = Room.objects.select_related(
            "chicken_house", "chicken_house__farm"
        ).order_by("chicken_house__farm__name", "chicken_house__name", "name")

        self.fields["status"].empty_label = _("Seleccione")
        self.fields["category"].empty_label = _("Seleccione")
        self.fields["position"].empty_label = _("Sin posición sugerida")
        self.fields["collaborator"].empty_label = _("Asignación automática")

    def _assign_css_classes(self) -> None:
        task_type_field = self.fields.get("task_type")
        if task_type_field:
            existing_class = task_type_field.widget.attrs.get("class", "")
            classes = set(existing_class.split())
            classes.update(FIELD_INPUT_CLASSES.split())
            task_type_field.widget.attrs["class"] = " ".join(sorted(filter(None, classes)))

    def clean_name(self) -> str:
        name: str = self.cleaned_data.get("name", "")  # type: ignore[assignment]
        if not name or not name.strip():
            raise forms.ValidationError(_("El nombre de la tarea es obligatorio."))
        return name.strip()

    def clean(self) -> dict[str, object]:
        cleaned_data = super().clean()
        task_type: str | None = cleaned_data.get("task_type")  # type: ignore[assignment]
        scheduled_for = cleaned_data.get("scheduled_for")

        for key in ("weekly_days", "month_days"):
            values: Iterable[int] | None = cleaned_data.get(key)  # type: ignore[assignment]
            cleaned_data[key] = sorted(set(values or []))

        cleaned_data["task_type"] = task_type or None
        cleaned_data["scheduled_for"] = scheduled_for or None
        task_type = cleaned_data["task_type"]  # normalize after casting

        if not task_type:
            cleaned_data["weekly_days"] = []
            cleaned_data["month_days"] = []
            return cleaned_data

        if task_type == TaskDefinition.TaskType.ONE_TIME:
            cleaned_data["weekly_days"] = []
            cleaned_data["month_days"] = []
            if not scheduled_for:
                self.add_error(
                    "scheduled_for",
                    _("Selecciona la fecha puntual para las tareas de ocurrencia única."),
                )
        elif task_type == TaskDefinition.TaskType.RECURRING:
            cleaned_data["scheduled_for"] = None
            if not cleaned_data["weekly_days"] and not cleaned_data["month_days"]:
                message = _(
                    "Selecciona al menos un día de la semana o del mes para las tareas recurrentes."
                )
                self.add_error("weekly_days", message)
                self.add_error("month_days", message)

        return cleaned_data

from __future__ import annotations

import json
from typing import Iterable

from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.utils.translation import gettext_lazy as _

from personal.models import DayOfWeek, PositionDefinition, UserProfile
from production.models import Room

from .models import MiniAppPushSubscription, TaskCategory, TaskDefinition, TaskStatus


FIELD_INPUT_CLASSES = (
    "mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700 "
    "focus:border-brand focus:outline-none focus:ring-2 focus:ring-brand/20"
)
TEXTAREA_CLASSES = FIELD_INPUT_CLASSES + " min-h-[120px] resize-y"
MULTISELECT_CLASSES = FIELD_INPUT_CLASSES + " h-36"
CHECKBOX_CLASSES = "h-4 w-4 rounded border-slate-300 text-brand focus:ring-brand focus:ring-offset-0"


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
            "is_mandatory",
            "is_accumulative",
            "criticality_level",
            "task_type",
            "scheduled_for",
            "weekly_days",
            "month_days",
            "position",
            "collaborator",
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
            "is_mandatory": forms.CheckboxInput(
                attrs={
                    "class": CHECKBOX_CLASSES,
                }
            ),
            "is_accumulative": forms.CheckboxInput(
                attrs={
                    "class": CHECKBOX_CLASSES,
                }
            ),
            "criticality_level": forms.Select(attrs={"class": FIELD_INPUT_CLASSES}),
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
            "rooms": forms.SelectMultiple(attrs={"class": MULTISELECT_CLASSES}),
            "evidence_requirement": forms.Select(attrs={"class": FIELD_INPUT_CLASSES}),
            "record_format": forms.Select(attrs={"class": FIELD_INPUT_CLASSES}),
        }
        labels = {
            "name": _("Nombre de la tarea"),
            "description": _("Descripción"),
            "status": _("Estado"),
            "category": _("Categoría"),
            "is_mandatory": _("Obligatoriedad"),
            "is_accumulative": _("Acumulable en mini app"),
            "criticality_level": _("Nivel de criticidad"),
            "task_type": _("Recurrencia"),
            "scheduled_for": _("Fecha puntual"),
            "position": _("Posición prioritaria"),
            "collaborator": _("Colaborador sugerido"),
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
            "is_mandatory": _(""),
            "is_accumulative": _(
                "Al activarse, la tarea se mostrará en la mini app aunque existan turnos nuevos sin completarse."
            ),
            "criticality_level": _(
                "Indica la severidad del impacto operativo si la tarea se omite."
            ),
            "rooms": _(
                "Selecciona los salones donde aplica la tarea. La granja y el galpón se infieren automáticamente."
            ),
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
            PositionDefinition.objects.select_related("farm", "chicken_house", "handoff_position")
            .order_by("display_order", "name")
        )
        self.fields["collaborator"].queryset = (
            UserProfile.objects.filter(is_active=True).order_by("apellidos", "nombres")
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


class MiniAppAuthenticationForm(AuthenticationForm):
    username = forms.CharField(
        label=_("Cédula"),
        widget=forms.TextInput(
            attrs={
                "autofocus": True,
                "autocomplete": "username",
                "inputmode": "numeric",
                "class": (
                    "w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm "
                    "font-medium text-slate-900 shadow-sm focus:border-brand focus:outline-none "
                    "focus:ring-2 focus:ring-brand/40"
                ),
                "placeholder": _("Ingresa tu cédula"),
            }
        ),
    )
    password = forms.CharField(
        label=_("Clave"),
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "current-password",
                "class": (
                    "w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm "
                    "font-medium text-slate-900 shadow-sm focus:border-brand focus:outline-none "
                    "focus:ring-2 focus:ring-brand/40"
                ),
                "placeholder": _("Ingresa tu clave"),
            }
        ),
    )

    error_messages = {
        "invalid_login": _(
            "Los datos ingresados no son válidos. Verifica la cédula y la clave."
        ),
        "inactive": _("Tu cuenta está inactiva. Contacta al administrador."),
        "no_mini_app_access": _("No tienes permisos para acceder a la mini app."),
    }

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if not user.has_perm("task_manager.access_mini_app"):
            raise forms.ValidationError(
                self.error_messages["no_mini_app_access"],
                code="no_mini_app_access",
            )


class MiniAppPushTestForm(forms.Form):
    user = forms.ModelChoiceField(
        label=_("Usuario"),
        queryset=UserProfile.objects.filter(is_active=True).order_by("apellidos", "nombres"),
        widget=forms.Select(attrs={"class": FIELD_INPUT_CLASSES}),
    )
    subscription = forms.ModelChoiceField(
        label=_("Dispositivo"),
        queryset=MiniAppPushSubscription.objects.none(),
        widget=forms.Select(attrs={"class": FIELD_INPUT_CLASSES}),
    )
    title = forms.CharField(
        label=_("Título"),
        max_length=120,
        initial="Granjas La Colina",
        widget=forms.TextInput(attrs={"class": FIELD_INPUT_CLASSES}),
    )
    body = forms.CharField(
        label=_("Mensaje"),
        max_length=250,
        widget=forms.Textarea(
            attrs={
                "class": TEXTAREA_CLASSES,
                "rows": 3,
                "placeholder": _("Ej. Nueva tarea prioritaria en tu turno."),
            }
        ),
    )
    action_url = forms.CharField(
        label=_("URL de destino"),
        required=False,
        initial="/task-manager/telegram/mini-app/?utm_source=push-test",
        widget=forms.TextInput(attrs={"class": FIELD_INPUT_CLASSES}),
    )
    icon_url = forms.CharField(
        label=_("Icono (opcional)"),
        required=False,
        widget=forms.TextInput(attrs={"class": FIELD_INPUT_CLASSES}),
    )
    badge_url = forms.CharField(
        label=_("Badge (opcional)"),
        required=False,
        widget=forms.TextInput(attrs={"class": FIELD_INPUT_CLASSES}),
    )
    tag = forms.CharField(
        label=_("Tag (opcional)"),
        required=False,
        max_length=64,
        widget=forms.TextInput(attrs={"class": FIELD_INPUT_CLASSES}),
    )
    require_interaction = forms.BooleanField(
        label=_("Mantener visible hasta interacción"),
        required=False,
        widget=forms.CheckboxInput(attrs={"class": CHECKBOX_CLASSES}),
    )
    ttl = forms.IntegerField(
        label=_("TTL (segundos)"),
        min_value=0,
        max_value=86400,
        initial=300,
        widget=forms.NumberInput(attrs={"class": FIELD_INPUT_CLASSES}),
    )
    data_payload = forms.CharField(
        label=_("Payload adicional (JSON)"),
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": TEXTAREA_CLASSES,
                "rows": 4,
                "placeholder": _('{"extra":"valor"}'),
            }
        ),
    )

    def __init__(self, *args, **kwargs):
        user_queryset = kwargs.pop("user_queryset", None)
        subscription_queryset = kwargs.pop("subscription_queryset", None)
        super().__init__(*args, **kwargs)

        if user_queryset is not None:
            self.fields["user"].queryset = user_queryset

        self.fields["user"].widget.attrs.setdefault("data-user-select", "true")
        self.fields["subscription"].queryset = MiniAppPushSubscription.objects.none()
        selected_user = self._resolve_selected_user()
        if selected_user:
            base_query = MiniAppPushSubscription.objects.filter(user=selected_user, is_active=True).order_by(
                "-updated_at"
            )
            if subscription_queryset is not None:
                base_query = subscription_queryset
            self.fields["subscription"].queryset = base_query

    def _resolve_selected_user(self) -> UserProfile | None:
        user_id = self.data.get("user") or self.initial.get("user")
        if not user_id:
            return None
        try:
            return UserProfile.objects.get(pk=user_id)
        except UserProfile.DoesNotExist:
            return None

    def clean_subscription(self) -> MiniAppPushSubscription:
        subscription = self.cleaned_data["subscription"]
        user = self.cleaned_data.get("user")
        if user and subscription.user_id != user.pk:
            raise forms.ValidationError(_("El dispositivo seleccionado no pertenece a este usuario."))
        return subscription

    def clean_data_payload(self) -> dict[str, object]:
        raw = self.cleaned_data.get("data_payload")
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise forms.ValidationError(_("JSON inválido: %(error)s"), params={"error": exc}) from exc
        if not isinstance(data, dict):
            raise forms.ValidationError(_("El payload debe ser un objeto JSON."))
        return data

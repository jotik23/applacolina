from __future__ import annotations

from datetime import date
from typing import Any

from django import forms
from django.apps import apps
from django.contrib.auth.forms import AuthenticationForm, ReadOnlyPasswordHashField
from django.db import IntegrityError, transaction
from django.db.models import IntegerField, Max
from django.db.models.functions import Cast

from .models import (
    AssignmentAlertLevel,
    CalendarStatus,
    OperatorRestPeriod,
    PositionDefinition,
    RestDayOfWeek,
    RestPeriodStatus,
    Role,
    ShiftAssignment,
    ShiftCalendar,
    ShiftType,
    UserProfile,
    resolve_overload_policy,
)
from production.models import Room


FIELD_INPUT_CLASSES = (
    "mt-2 block w-full rounded-xl border border-slate-200/80 bg-white/95 px-3 py-2 text-sm "
    "shadow-sm transition duration-150 focus:border-brand focus:outline-none focus:ring-2 "
    "focus:ring-brand/40 placeholder:text-slate-400 disabled:cursor-not-allowed disabled:opacity-60"
)
DATE_INPUT_CLASSES = FIELD_INPUT_CLASSES + " cursor-pointer"
TEXTAREA_CLASSES = FIELD_INPUT_CLASSES + " min-h-[120px] resize-y"


class CalendarGenerationForm(forms.Form):
    name = forms.CharField(
        label="Nombre",
        max_length=150,
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": FIELD_INPUT_CLASSES,
            }
        ),
    )
    start_date = forms.DateField(
        label="Fecha de inicio",
        widget=forms.DateInput(
            attrs={
                "type": "date",
                "class": DATE_INPUT_CLASSES,
            }
        ),
    )
    end_date = forms.DateField(
        label="Fecha de fin",
        widget=forms.DateInput(
            attrs={
                "type": "date",
                "class": DATE_INPUT_CLASSES,
            }
        ),
    )
    notes = forms.CharField(
        label="Notas",
        required=False,
        widget=forms.Textarea(
            attrs={
                "rows": 3,
                "class": TEXTAREA_CLASSES,
                "placeholder": "Observaciones generales o eventos programados",
            }
        ),
    )

    error_messages = {
        "overlap": "Ya existe un calendario registrado que se solapa con el rango seleccionado.",
        "date_order": "La fecha final debe ser posterior o igual a la fecha inicial.",
    }

    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean()
        start_date = cleaned_data.get("start_date")
        end_date = cleaned_data.get("end_date")

        if start_date and end_date and end_date < start_date:
            raise forms.ValidationError(self.error_messages["date_order"])

        if start_date and end_date:
            overlap_exists = ShiftCalendar.objects.filter(
                status__in=[
                    CalendarStatus.DRAFT,
                    CalendarStatus.APPROVED,
                    CalendarStatus.MODIFIED,
                ]
            ).filter(
                start_date__lte=end_date,
                end_date__gte=start_date,
            ).exists()

            if overlap_exists:
                raise forms.ValidationError(self.error_messages["overlap"])

        return cleaned_data


class BaseAssignmentForm(forms.Form):
    operator_id = forms.IntegerField(widget=forms.Select())
    force_override = forms.BooleanField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, calendar: ShiftCalendar, **kwargs):
        self.calendar = calendar
        super().__init__(*args, **kwargs)

    def _get_operator(self, operator_id: int, *, target_date: date | None = None) -> UserProfile:
        try:
            operator = UserProfile.objects.get(pk=operator_id)
        except UserProfile.DoesNotExist as exc:  # pragma: no cover - defensive
            raise forms.ValidationError("El operario seleccionado no existe.") from exc

        if target_date and not operator.is_active_on(target_date):
            raise forms.ValidationError("El operario no está activo en la fecha seleccionada.")

        return operator

    def _resolve_assignment_outcome(
        self,
        operator: UserProfile,
        position: PositionDefinition,
        target_date,
        *,
        exclude_assignment: ShiftAssignment | None = None,
        allow_override: bool = False,
        reset_conflicts: bool = False,
    ) -> tuple[
        AssignmentAlertLevel,
        bool,
        int,
        ShiftAssignment | None,
        list[OperatorRestPeriod],
    ]:
        alert_level = AssignmentAlertLevel.NONE
        is_overtime = False
        overtime_points = 0
        lacks_authorization = False
        conflicting_assignment: ShiftAssignment | None = None
        conflicting_rest_periods: list[OperatorRestPeriod] = []

        if not position.is_active_on(target_date):
            raise forms.ValidationError("La posición no está vigente para la fecha seleccionada.")

        has_suggestion = operator.suggested_positions.filter(pk=position.pk).exists()
        if not has_suggestion:
            if not allow_override:
                raise forms.ValidationError(
                    "El operario no tiene esta posición sugerida para la fecha seleccionada."
                )
            lacks_authorization = True

        if allow_override and lacks_authorization:
            alert_level = AssignmentAlertLevel.WARN

        conflict_qs = ShiftAssignment.objects.filter(
            calendar=self.calendar,
            date=target_date,
            operator=operator,
        ).exclude(pk=getattr(exclude_assignment, "pk", None))
        if reset_conflicts:
            conflicting_assignment = conflict_qs.select_related("position").first()
            conflict = bool(conflicting_assignment)
        else:
            conflict = conflict_qs.exists()

        if conflict and not reset_conflicts:
            raise forms.ValidationError(
                "El operario ya tiene un turno asignado en esta fecha dentro del personal. "
                "Debes liberar ese turno antes de reasignarlo."
            )

        if reset_conflicts:
            conflicting_rest_periods = list(
                OperatorRestPeriod.objects.filter(
                    operator=operator,
                    start_date__lte=target_date,
                    end_date__gte=target_date,
                ).exclude(status=RestPeriodStatus.CANCELLED)
            )

        if is_overtime:
            policy = resolve_overload_policy(position.category)
            overtime_points = policy.overtime_points

        return (
            alert_level,
            is_overtime,
            overtime_points,
            conflicting_assignment,
            conflicting_rest_periods,
        )


class AssignmentUpdateForm(BaseAssignmentForm):
    assignment_id = forms.IntegerField(widget=forms.HiddenInput)

    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean()
        assignment_id = cleaned_data.get("assignment_id")
        operator_id = cleaned_data.get("operator_id")

        if not assignment_id or not operator_id:
            raise forms.ValidationError("Datos incompletos para actualizar la asignación.")

        try:
            assignment = ShiftAssignment.objects.select_related("position").get(
                pk=assignment_id,
                calendar=self.calendar,
            )
        except ShiftAssignment.DoesNotExist as exc:
            raise forms.ValidationError("La asignación indicada no existe en este personal.") from exc

        operator = self._get_operator(operator_id, target_date=assignment.date)
        allow_override = bool(cleaned_data.get("force_override"))
        (
            alert_level,
            is_overtime,
            overtime_points,
            conflicting_assignment,
            conflicting_rest_periods,
        ) = self._resolve_assignment_outcome(
            operator,
            assignment.position,
            assignment.date,
            exclude_assignment=assignment,
            allow_override=allow_override,
            reset_conflicts=True,
        )

        cleaned_data["assignment"] = assignment
        cleaned_data["operator"] = operator
        cleaned_data["alert_level"] = alert_level
        cleaned_data["is_overtime"] = is_overtime
        cleaned_data["overtime_points"] = overtime_points
        cleaned_data["conflicting_assignment"] = conflicting_assignment
        cleaned_data["conflicting_rest_periods"] = conflicting_rest_periods
        return cleaned_data


class AssignmentCreateForm(BaseAssignmentForm):
    position_id = forms.IntegerField(widget=forms.HiddenInput)
    date = forms.DateField(widget=forms.HiddenInput)

    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean()
        position_id = cleaned_data.get("position_id")
        operator_id = cleaned_data.get("operator_id")
        target_date = cleaned_data.get("date")

        if not position_id or not operator_id or not target_date:
            raise forms.ValidationError("Datos incompletos para crear la asignación.")

        try:
            position = PositionDefinition.objects.get(pk=position_id)
        except PositionDefinition.DoesNotExist as exc:
            raise forms.ValidationError("La posición seleccionada no existe.") from exc

        if not (self.calendar.start_date <= target_date <= self.calendar.end_date):
            raise forms.ValidationError("La fecha está fuera del rango del personal.")

        exists = ShiftAssignment.objects.filter(
            calendar=self.calendar,
            position=position,
            date=target_date,
        ).exists()
        if exists:
            raise forms.ValidationError("Ya existe una asignación para esta posición en la fecha indicada.")

        operator = self._get_operator(operator_id, target_date=target_date)
        allow_override = bool(cleaned_data.get("force_override"))
        (
            alert_level,
            is_overtime,
            overtime_points,
            conflicting_assignment,
            conflicting_rest_periods,
        ) = self._resolve_assignment_outcome(
            operator,
            position,
            target_date,
            allow_override=allow_override,
            reset_conflicts=True,
        )

        cleaned_data["position"] = position
        cleaned_data["operator"] = operator
        cleaned_data["alert_level"] = alert_level
        cleaned_data["target_date"] = target_date
        cleaned_data["is_overtime"] = is_overtime
        cleaned_data["overtime_points"] = overtime_points
        cleaned_data["conflicting_assignment"] = conflicting_assignment
        cleaned_data["conflicting_rest_periods"] = conflicting_rest_periods
        return cleaned_data


class PositionDefinitionForm(forms.ModelForm):
    rooms = forms.ModelMultipleChoiceField(
        queryset=Room.objects.select_related("chicken_house", "chicken_house__farm").order_by(
            "chicken_house__farm__name", "chicken_house__name", "name"
        ),
        required=False,
    )

    class Meta:
        model = PositionDefinition
        fields = [
            "name",
            "category",
            "farm",
            "chicken_house",
            "rooms",
            "valid_from",
            "valid_until",
            "handoff_position",
        ]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        handoff_field = self.fields.get("handoff_position")
        if handoff_field:
            queryset = PositionDefinition.objects.order_by("display_order", "name")
            if self.instance.pk:
                queryset = queryset.exclude(pk=self.instance.pk)
            handoff_field.queryset = queryset
            handoff_field.required = False

    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean()
        category = cleaned_data.get("category")
        chicken_house = cleaned_data.get("chicken_house")
        rooms_value = cleaned_data.get("rooms")  # type: ignore[assignment]
        selected_rooms: list[Room] = list(rooms_value) if rooms_value is not None else []
        if selected_rooms:
            if not chicken_house:
                self.add_error(
                    "rooms",
                    "Debe seleccionar un galpón para asociar salones a la posición.",
                )
            else:
                invalid_rooms = [room for room in selected_rooms if room.chicken_house_id != chicken_house.id]
                if invalid_rooms:
                    self.add_error(
                        "rooms",
                        "Todos los salones deben pertenecer al galpón seleccionado.",
                    )
        handoff_position = cleaned_data.get("handoff_position")
        farm = cleaned_data.get("farm")
        if handoff_position and self.instance.pk and handoff_position.pk == self.instance.pk:
            self.add_error("handoff_position", "La posición no puede entregarse turno a sí misma.")
        if handoff_position and farm and handoff_position.farm_id != farm.id:
            self.add_error(
                "handoff_position",
                "La posición de entrega debe pertenecer a la misma granja.",
            )
        if rooms_value is not None:
            self.instance._pending_rooms_for_validation = selected_rooms
        return cleaned_data

    @staticmethod
    def _generate_code() -> str:
        numeric_codes = (
            PositionDefinition.objects.filter(code__regex=r"^\d+$")
            .annotate(code_int=Cast("code", IntegerField()))
            .aggregate(max_code=Max("code_int"))
        )
        current_max = numeric_codes.get("max_code") or 0
        candidate = current_max + 1
        # Ensure uniqueness in case non-numeric codes collide with numeric range
        while PositionDefinition.objects.filter(code=str(candidate)).exists():
            candidate += 1
        return str(candidate)

    def save(self, commit: bool = True) -> PositionDefinition:
        instance: PositionDefinition = super().save(commit=False)
        is_new = instance.pk is None

        if not commit:
            if is_new and not instance.code:
                instance.code = self._generate_code()
            return instance

        try:
            if is_new:
                while True:
                    try:
                        with transaction.atomic():
                            if not instance.display_order:
                                max_order = (
                                    PositionDefinition.objects.aggregate(
                                        max_order=Max("display_order")
                                    ).get("max_order")
                                    or 0
                                )
                                instance.display_order = max_order + 1
                            if not instance.code:
                                instance.code = self._generate_code()
                            instance.save()
                        break
                    except IntegrityError:
                        instance.code = None
            else:
                instance.save()

            self.save_m2m()
            return instance
        finally:
            if hasattr(instance, "_pending_rooms_for_validation"):
                delattr(instance, "_pending_rooms_for_validation")


class OperatorProfileForm(forms.ModelForm):
    access_key = forms.CharField(
        label="Clave de acceso",
        required=False,
        strip=False,
        max_length=128,
    )
    roles = forms.ModelMultipleChoiceField(
        queryset=Role.objects.order_by("name"),
        required=False,
    )
    suggested_positions = forms.ModelMultipleChoiceField(
        queryset=PositionDefinition.objects.none(),
        required=False,
    )
    automatic_rest_days = forms.MultipleChoiceField(
        label="Días de descanso automático",
        required=False,
        choices=RestDayOfWeek.choices,
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = UserProfile
        fields = [
            "cedula",
            "nombres",
            "apellidos",
            "telefono",
            "employment_start_date",
            "employment_end_date",
            "automatic_rest_days",
            "suggested_positions",
            "roles",
        ]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        employment_field = self.fields.get("employment_start_date")
        if employment_field:
            employment_field.widget.input_type = "date"
            employment_field.required = False
        employment_end_field = self.fields.get("employment_end_date")
        if employment_end_field:
            employment_end_field.widget.input_type = "date"
            employment_end_field.required = False
        suggested_field = self.fields.get("suggested_positions")
        if suggested_field:
            today = UserProfile.colombia_today()
            suggested_field.queryset = (
                PositionDefinition.objects.active_on(today)
                .order_by(
                    "display_order",
                    "name",
                )
            )
        rest_field = self.fields.get("automatic_rest_days")
        existing_values = getattr(self.instance, "automatic_rest_days", None)
        if rest_field and existing_values:
            rest_field.initial = [str(value) for value in existing_values]

    def clean_access_key(self) -> str:
        value = self.cleaned_data.get("access_key") or ""
        return value.strip()

    def clean_cedula(self) -> str:
        cedula = self.cleaned_data.get("cedula", "")
        return cedula.strip()

    def clean_telefono(self) -> str:
        telefono = self.cleaned_data.get("telefono", "")
        return telefono.strip()

    def clean_automatic_rest_days(self) -> list[int]:
        values = self.cleaned_data.get("automatic_rest_days") or []
        return [int(value) for value in values]

    def save(self, commit: bool = True) -> UserProfile:
        instance: UserProfile = super().save(commit=False)
        access_key = self.cleaned_data.get("access_key") or ""
        if access_key:
            instance.set_password(access_key)
        elif not instance.pk:
            instance.set_unusable_password()

        if commit:
            instance.save()
            self.save_m2m()

        return instance


class OperatorRestPeriodForm(forms.ModelForm):
    class Meta:
        model = OperatorRestPeriod
        fields = [
            "operator",
            "start_date",
            "end_date",
            "status",
            "source",
            "calendar",
            "notes",
        ]

    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean()
        start = cleaned_data.get("start_date")
        end = cleaned_data.get("end_date")
        if start and end and end < start:
            self.add_error("end_date", "La fecha final debe ser posterior o igual al inicio.")
        return cleaned_data


class PortalAuthenticationForm(AuthenticationForm):
    username = forms.CharField(
        label="Cédula",
        widget=forms.TextInput(
            attrs={
                "autofocus": True,
                "autocomplete": "username",
                "class": "block w-full rounded border border-slate-300 px-3 py-2 text-slate-900 focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand",
                "placeholder": "Ingresa la cédula",
            }
        ),
    )
    password = forms.CharField(
        label="Clave",
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "current-password",
                "class": "block w-full rounded border border-slate-300 px-3 py-2 text-slate-900 focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand",
                "placeholder": "Ingresa la clave",
            }
        ),
    )

    error_messages = {
        "invalid_login": (
            "Los datos ingresados no son válidos. Verifica la cédula y la clave."
        ),
        "inactive": "Tu cuenta está inactiva. Contacta al administrador.",
    }


class UserCreationForm(forms.ModelForm):
    password1 = forms.CharField(
        label="Clave",
        widget=forms.PasswordInput,
        strip=False,
    )
    password2 = forms.CharField(
        label="Confirmar clave",
        widget=forms.PasswordInput,
        strip=False,
    )
    automatic_rest_days = forms.MultipleChoiceField(
        label="Días de descanso automático",
        required=False,
        choices=RestDayOfWeek.choices,
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = UserProfile
        fields = [
            "cedula",
            "nombres",
            "apellidos",
            "telefono",
            "direccion",
            "suggested_positions",
            "employment_start_date",
            "employment_end_date",
            "automatic_rest_days",
            "contacto_nombre",
            "contacto_telefono",
            "roles",
            "groups",
            "is_active",
            "is_staff",
        ]
        widgets = {
            "roles": forms.CheckboxSelectMultiple,
            "groups": forms.CheckboxSelectMultiple,
            "suggested_positions": forms.CheckboxSelectMultiple,
        }

    def clean_cedula(self):
        cedula = self.cleaned_data["cedula"].strip()
        if UserProfile.objects.filter(cedula=cedula).exists():
            raise forms.ValidationError("Esta cedula ya se encuentra registrada.")
        return cedula

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name in ("employment_start_date", "employment_end_date"):
            field = self.fields.get(field_name)
            if field:
                field.widget = forms.DateInput(attrs={"type": "date"})
                field.required = False
        suggested_field = self.fields.get("suggested_positions")
        if suggested_field:
            position_model = apps.get_model("personal", "PositionDefinition")
            suggested_field.queryset = position_model.objects.order_by("name")
        rest_values = getattr(self.instance, "automatic_rest_days", None)
        if rest_values:
            self.initial["automatic_rest_days"] = [str(value) for value in rest_values]

    def clean_password2(self):
        password1 = self.cleaned_data.get("password1")
        password2 = self.cleaned_data.get("password2")
        if password1 and password2 and password1 != password2:
            raise forms.ValidationError("Las claves no coinciden.")
        return password2

    def clean_automatic_rest_days(self):
        values = self.cleaned_data.get("automatic_rest_days") or []
        return [int(value) for value in values]

    def save(self, commit: bool = True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
            self.save_m2m()
        return user


class UserChangeForm(forms.ModelForm):
    password = ReadOnlyPasswordHashField(
        label="Clave",
        help_text=(
            "Las claves no se almacenan en texto plano. "
            "Puedes restablecer la clave usando el formulario correspondiente."
        ),
    )
    automatic_rest_days = forms.MultipleChoiceField(
        label="Días de descanso automático",
        required=False,
        choices=RestDayOfWeek.choices,
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = UserProfile
        fields = [
            "cedula",
            "nombres",
            "apellidos",
            "telefono",
            "direccion",
            "suggested_positions",
            "employment_start_date",
            "employment_end_date",
            "automatic_rest_days",
            "contacto_nombre",
            "contacto_telefono",
            "roles",
            "groups",
            "is_active",
            "is_staff",
            "password",
        ]
        widgets = {
            "roles": forms.CheckboxSelectMultiple,
            "groups": forms.CheckboxSelectMultiple,
            "suggested_positions": forms.CheckboxSelectMultiple,
        }

    def clean_password(self):
        return self.initial.get("password")

    def clean_automatic_rest_days(self):
        values = self.cleaned_data.get("automatic_rest_days") or []
        return [int(value) for value in values]

    def clean_cedula(self):
        cedula = self.cleaned_data["cedula"].strip()
        qs = UserProfile.objects.filter(cedula=cedula)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("Esta cedula ya se encuentra registrada.")
        return cedula

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name in ("employment_start_date", "employment_end_date"):
            field = self.fields.get(field_name)
            if field:
                field.widget = forms.DateInput(attrs={"type": "date"})
                field.required = False
        suggested_field = self.fields.get("suggested_positions")
        if suggested_field:
            position_model = apps.get_model("personal", "PositionDefinition")
            suggested_field.queryset = position_model.objects.order_by("name")
        rest_values = getattr(self.instance, "automatic_rest_days", None)
        if rest_values:
            self.initial["automatic_rest_days"] = [str(value) for value in rest_values]

from __future__ import annotations

from typing import Any

from django import forms
from django.db import IntegrityError, transaction
from django.db.models import IntegerField, Max
from django.db.models.functions import Cast

from .models import (
    AssignmentAlertLevel,
    CalendarStatus,
    OperatorCapability,
    PositionCategory,
    PositionDefinition,
    ShiftAssignment,
    ShiftCalendar,
    ShiftType,
    required_skill_for_complexity,
    resolve_overload_policy,
)
from users.models import Role, UserProfile
from granjas.models import Farm, Room


class CalendarGenerationForm(forms.Form):
    name = forms.CharField(
        label="Nombre",
        max_length=150,
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "mt-1 block w-full rounded border border-slate-200 bg-white focus:border-amber-500 focus:ring-amber-500",
                "placeholder": "Semana 42 - Colina",
            }
        ),
    )
    start_date = forms.DateField(
        label="Fecha de inicio",
        widget=forms.DateInput(
            attrs={
                "type": "date",
                "class": "mt-1 block w-full rounded border border-slate-200 bg-white focus:border-amber-500 focus:ring-amber-500",
            }
        ),
    )
    end_date = forms.DateField(
        label="Fecha de fin",
        widget=forms.DateInput(
            attrs={
                "type": "date",
                "class": "mt-1 block w-full rounded border border-slate-200 bg-white focus:border-amber-500 focus:ring-amber-500",
            }
        ),
    )
    notes = forms.CharField(
        label="Notas",
        required=False,
        widget=forms.Textarea(
            attrs={
                "rows": 3,
                "class": "mt-1 block w-full rounded border border-slate-200 bg-white focus:border-amber-500 focus:ring-amber-500",
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

    def _get_operator(self, operator_id: int) -> UserProfile:
        try:
            return UserProfile.objects.get(pk=operator_id, is_active=True)
        except UserProfile.DoesNotExist as exc:  # pragma: no cover - defensive
            raise forms.ValidationError("El operario seleccionado no existe.") from exc

    def _resolve_assignment_outcome(
        self,
        operator: UserProfile,
        position: PositionDefinition,
        target_date,
        *,
        exclude_assignment: ShiftAssignment | None = None,
        allow_override: bool = False,
    ) -> tuple[AssignmentAlertLevel, bool, int]:
        alert_level = AssignmentAlertLevel.NONE
        is_overtime = False
        overtime_points = 0

        if not position.is_active_on(target_date):
            raise forms.ValidationError("La posición no está vigente para la fecha seleccionada.")

        required_score = required_skill_for_complexity(position.complexity)
        capability = OperatorCapability.objects.filter(
            operator=operator, category=position.category
        ).first()

        if not capability:
            if not allow_override:
                raise forms.ValidationError(
                    "El operario no está habilitado para esta posición en la fecha seleccionada."
                )
            alert_level = AssignmentAlertLevel.CRITICAL
        else:
            skill_score = capability.skill_score

            if skill_score < required_score and not position.allow_lower_complexity:
                if not allow_override:
                    raise forms.ValidationError(
                        "La posición requiere un nivel de criticidad superior y no admite coberturas inferiores."
                    )
                alert_level = AssignmentAlertLevel.CRITICAL
            elif skill_score < required_score:
                diff = required_score - skill_score
                alert_level = (
                    AssignmentAlertLevel.WARN
                    if diff == 1
                    else AssignmentAlertLevel.CRITICAL
                )
            else:
                alert_level = AssignmentAlertLevel.NONE

        conflict = (
            ShiftAssignment.objects.filter(
                calendar=self.calendar,
                date=target_date,
                operator=operator,
            )
            .exclude(pk=getattr(exclude_assignment, "pk", None))
            .exists()
        )
        if conflict:
            if not allow_override:
                raise forms.ValidationError(
                    "El operario ya tiene un turno asignado en esta fecha dentro del calendario."
                )
            is_overtime = True
            alert_level = AssignmentAlertLevel.CRITICAL

        if is_overtime:
            policy = resolve_overload_policy(position.category)
            overtime_points = policy.overtime_points

        return alert_level, is_overtime, overtime_points


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
            raise forms.ValidationError("La asignación indicada no existe en este calendario.") from exc

        operator = self._get_operator(operator_id)
        allow_override = bool(cleaned_data.get("force_override"))
        alert_level, is_overtime, overtime_points = self._resolve_assignment_outcome(
            operator,
            assignment.position,
            assignment.date,
            exclude_assignment=assignment,
            allow_override=allow_override,
        )

        cleaned_data["assignment"] = assignment
        cleaned_data["operator"] = operator
        cleaned_data["alert_level"] = alert_level
        cleaned_data["is_overtime"] = is_overtime
        cleaned_data["overtime_points"] = overtime_points
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
            raise forms.ValidationError("La fecha está fuera del rango del calendario.")

        exists = ShiftAssignment.objects.filter(
            calendar=self.calendar,
            position=position,
            date=target_date,
        ).exists()
        if exists:
            raise forms.ValidationError("Ya existe una asignación para esta posición en la fecha indicada.")

        operator = self._get_operator(operator_id)
        allow_override = bool(cleaned_data.get("force_override"))
        alert_level, is_overtime, overtime_points = self._resolve_assignment_outcome(
            operator,
            position,
            target_date,
            allow_override=allow_override,
        )

        cleaned_data["position"] = position
        cleaned_data["operator"] = operator
        cleaned_data["alert_level"] = alert_level
        cleaned_data["target_date"] = target_date
        cleaned_data["is_overtime"] = is_overtime
        cleaned_data["overtime_points"] = overtime_points
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
            "complexity",
            "allow_lower_complexity",
            "valid_from",
            "valid_until",
            "is_active",
            "notes",
        ]

    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean()
        category = cleaned_data.get("category")
        chicken_house = cleaned_data.get("chicken_house")
        rooms = cleaned_data.get("rooms")  # type: ignore[assignment]
        if rooms:
            if not chicken_house:
                self.add_error(
                    "rooms",
                    "Debe seleccionar un galpón para asociar salones a la posición.",
                )
            else:
                invalid_rooms = [room for room in rooms if room.chicken_house_id != chicken_house.id]
                if invalid_rooms:
                    self.add_error(
                        "rooms",
                        "Todos los salones deben pertenecer al galpón seleccionado.",
                    )
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


class OperatorProfileForm(forms.ModelForm):
    roles = forms.ModelMultipleChoiceField(
        queryset=Role.objects.order_by("name"),
        required=False,
    )
    preferred_farm = forms.ModelChoiceField(
        queryset=Farm.objects.order_by("name"),
        required=False,
        empty_label="(sin preferencia)",
    )

    class Meta:
        model = UserProfile
        fields = [
            "cedula",
            "nombres",
            "apellidos",
            "telefono",
            "email",
            "employment_start_date",
            "preferred_farm",
            "roles",
            "is_active",
        ]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        employment_field = self.fields.get("employment_start_date")
        if employment_field:
            employment_field.widget.input_type = "date"
            employment_field.required = False

    def clean_cedula(self) -> str:
        cedula = self.cleaned_data.get("cedula", "")
        return cedula.strip()

    def clean_telefono(self) -> str:
        telefono = self.cleaned_data.get("telefono", "")
        return telefono.strip()

    def save(self, commit: bool = True) -> UserProfile:
        instance: UserProfile = super().save(commit=False)
        if not instance.pk:
            instance.set_unusable_password()

        if commit:
            instance.save()
            self.save_m2m()

        return instance


class OperatorCapabilityForm(forms.ModelForm):
    class Meta:
        model = OperatorCapability
        fields = [
            "operator",
            "category",
            "skill_score",
        ]

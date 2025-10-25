from __future__ import annotations

from typing import Any

from django import forms
from django.db.models import Q

from .models import (
    AssignmentAlertLevel,
    CalendarStatus,
    OperatorCapability,
    PositionDefinition,
    ShiftAssignment,
    ShiftCalendar,
    complexity_score,
)
from users.models import UserProfile


class CalendarGenerationForm(forms.Form):
    name = forms.CharField(
        label="Nombre del calendario",
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

    def __init__(self, *args, calendar: ShiftCalendar, **kwargs):
        self.calendar = calendar
        super().__init__(*args, **kwargs)

    def _get_operator(self, operator_id: int) -> UserProfile:
        try:
            return UserProfile.objects.get(pk=operator_id, is_active=True)
        except UserProfile.DoesNotExist as exc:  # pragma: no cover - defensive
            raise forms.ValidationError("El operario seleccionado no existe.") from exc

    def _resolve_alert_level(
        self,
        operator: UserProfile,
        position: PositionDefinition,
        target_date,
        *,
        exclude_assignment: ShiftAssignment | None = None,
    ) -> AssignmentAlertLevel:
        capabilities = (
            OperatorCapability.objects.filter(operator=operator, category=position.category)
            .filter(effective_from__lte=target_date)
            .filter(Q(effective_until__isnull=True) | Q(effective_until__gte=target_date))
        )

        active_caps = [cap for cap in capabilities if cap.is_active_on(target_date)]
        if not active_caps:
            raise forms.ValidationError(
                "El operario no está habilitado para esta posición en la fecha seleccionada."
            )

        best_cap = max(active_caps, key=lambda cap: complexity_score(cap.max_complexity))
        required_score = complexity_score(position.complexity)
        max_score = complexity_score(best_cap.max_complexity)

        if max_score < required_score and not position.allow_lower_complexity:
            raise forms.ValidationError(
                "La posición requiere mayor complejidad y no admite coberturas inferiores."
            )

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
            raise forms.ValidationError(
                "El operario ya tiene un turno asignado en esta fecha dentro del calendario."
            )

        if max_score >= required_score:
            return AssignmentAlertLevel.NONE

        diff = required_score - max_score
        return AssignmentAlertLevel.WARN if diff == 1 else AssignmentAlertLevel.CRITICAL


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
        alert_level = self._resolve_alert_level(
            operator,
            assignment.position,
            assignment.date,
            exclude_assignment=assignment,
        )

        cleaned_data["assignment"] = assignment
        cleaned_data["operator"] = operator
        cleaned_data["alert_level"] = alert_level
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
        alert_level = self._resolve_alert_level(operator, position, target_date)

        cleaned_data["position"] = position
        cleaned_data["operator"] = operator
        cleaned_data["alert_level"] = alert_level
        cleaned_data["target_date"] = target_date
        return cleaned_data

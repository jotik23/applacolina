from __future__ import annotations

from django import forms
from django.contrib.auth.forms import AuthenticationForm, ReadOnlyPasswordHashField
from django.apps import apps

from .models import Role, UserProfile, RestDayOfWeek


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
            position_model = apps.get_model("calendario", "PositionDefinition")
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
            position_model = apps.get_model("calendario", "PositionDefinition")
            suggested_field.queryset = position_model.objects.order_by("name")
        rest_values = getattr(self.instance, "automatic_rest_days", None)
        if rest_values:
            self.initial["automatic_rest_days"] = [str(value) for value in rest_values]

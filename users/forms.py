from __future__ import annotations

from django import forms
from django.contrib.auth.forms import ReadOnlyPasswordHashField

from .models import Role, UserProfile


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

    class Meta:
        model = UserProfile
        fields = [
            "cedula",
            "nombres",
            "apellidos",
            "telefono",
            "email",
            "direccion",
            "preferred_farm",
            "employment_start_date",
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
        }

    def clean_cedula(self):
        cedula = self.cleaned_data["cedula"].strip()
        if UserProfile.objects.filter(cedula=cedula).exists():
            raise forms.ValidationError("Esta cedula ya se encuentra registrada.")
        return cedula

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        employment_field = self.fields.get("employment_start_date")
        if employment_field:
            employment_field.widget = forms.DateInput(attrs={"type": "date"})
            employment_field.required = False

    def clean_password2(self):
        password1 = self.cleaned_data.get("password1")
        password2 = self.cleaned_data.get("password2")
        if password1 and password2 and password1 != password2:
            raise forms.ValidationError("Las claves no coinciden.")
        return password2

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

    class Meta:
        model = UserProfile
        fields = [
            "cedula",
            "nombres",
            "apellidos",
            "telefono",
            "email",
            "direccion",
            "preferred_farm",
            "employment_start_date",
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
        }

    def clean_password(self):
        return self.initial.get("password")

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
        employment_field = self.fields.get("employment_start_date")
        if employment_field:
            employment_field.widget = forms.DateInput(attrs={"type": "date"})
            employment_field.required = False

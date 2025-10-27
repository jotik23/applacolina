from __future__ import annotations

from django.db import migrations, models


def assign_employment_start_dates(apps, schema_editor) -> None:
    UserProfile = apps.get_model("users", "UserProfile")
    for user in UserProfile.objects.all():
        if user.employment_start_date or not user.date_joined:
            continue
        user.employment_start_date = user.date_joined.date()
        user.save(update_fields=["employment_start_date"])


def remove_employment_start_dates(apps, schema_editor) -> None:
    UserProfile = apps.get_model("users", "UserProfile")
    UserProfile.objects.update(employment_start_date=None)


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0002_userprofile_preferred_farm"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="employment_start_date",
            field=models.DateField(
                blank=True,
                help_text="Fecha desde la cual el colaborador se considera activo para turnos y descansos.",
                null=True,
                verbose_name="Fecha de ingreso",
            ),
        ),
        migrations.RunPython(assign_employment_start_dates, remove_employment_start_dates),
    ]

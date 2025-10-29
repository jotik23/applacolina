from __future__ import annotations

from datetime import date

from django.contrib.auth.base_user import BaseUserManager
from django.db import models
from django.db.models import Q


class UserProfileQuerySet(models.QuerySet):
    """Custom queryset helpers for UserProfile."""

    def active_on(self, target_date: date) -> "UserProfileQuerySet":
        """Filter collaborators that are active on the given date."""

        if not target_date:
            return self.none()

        return self.filter(
            Q(employment_start_date__isnull=True) | Q(employment_start_date__lte=target_date),
            Q(employment_end_date__isnull=True) | Q(employment_end_date__gte=target_date),
        )


class UserProfileManager(BaseUserManager):
    """Custom manager for the UserProfile model."""

    use_in_migrations = True

    def get_queryset(self):  # type: ignore[override]
        return UserProfileQuerySet(self.model, using=self._db)

    def active_on(self, target_date: date):
        return self.get_queryset().active_on(target_date)

    def _create_user(self, cedula: str, password: str | None, **extra_fields):
        if not cedula:
            raise ValueError("El usuario debe tener una cedula definida.")
        cedula = cedula.strip()
        user = self.model(cedula=cedula, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_user(self, cedula: str, password: str | None = None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(cedula, password, **extra_fields)

    def create_superuser(self, cedula: str, password: str | None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Los superusuarios deben tener is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Los superusuarios deben tener is_superuser=True.")
        return self._create_user(cedula, password, **extra_fields)

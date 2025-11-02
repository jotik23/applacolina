from __future__ import annotations

from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.models import Permission


class RoleAwareModelBackend(ModelBackend):
    """Authentication backend that also grants permissions defined in personal.Role."""

    def get_role_permissions(self, user_obj, obj=None) -> set[str]:
        if (
            not getattr(user_obj, "is_active", False)
            or getattr(user_obj, "is_anonymous", False)
            or obj is not None
        ):
            return set()

        cache_name = "_role_perm_cache"
        cached = getattr(user_obj, cache_name, None)
        if cached is not None:
            return cached

        qs = (
            Permission.objects.filter(role_permissions__role__usuarios=user_obj)
            .values_list("content_type__app_label", "codename")
            .order_by()
        )
        perms = {f"{app_label}.{codename}" for app_label, codename in qs}
        setattr(user_obj, cache_name, perms)
        return perms

    def get_all_permissions(self, user_obj, obj=None):
        perms = super().get_all_permissions(user_obj, obj=obj)
        return perms | self.get_role_permissions(user_obj, obj=obj)

    def get_user_permissions(self, user_obj, obj=None):
        perms = super().get_user_permissions(user_obj, obj=obj)
        if obj is not None:
            return perms
        return perms | self.get_role_permissions(user_obj, obj=obj)

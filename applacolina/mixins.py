from django.contrib.auth.mixins import (
    LoginRequiredMixin,
    PermissionRequiredMixin,
    UserPassesTestMixin,
)
from django.shortcuts import redirect
from django.urls import reverse_lazy


class StaffRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Mixin that restricts access to authenticated staff users only."""

    login_url = reverse_lazy("portal:login")
    raise_exception = False

    def test_func(self):
        user = self.request.user
        return bool(user and user.is_authenticated and user.is_staff)

    def handle_no_permission(self):
        user = getattr(self.request, "user", None)
        if user and user.is_authenticated and not getattr(user, "is_staff", False):
            return redirect("task_manager:telegram-mini-app")
        return super().handle_no_permission()


class EggInventoryPermissionMixin(LoginRequiredMixin, PermissionRequiredMixin):
    """Allow access to egg inventory for staff or users with explicit permission."""

    login_url = reverse_lazy("portal:login")
    permission_required = "production.access_egg_inventory"
    raise_exception = False

    def has_permission(self):
        user = getattr(self.request, "user", None)
        if not user or not user.is_authenticated:
            return False
        if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
            return True
        required_perms = self.get_permission_required()
        if isinstance(required_perms, str):
            required_perms = (required_perms,)
        return all(user.has_perm(perm) for perm in required_perms)

    def handle_no_permission(self):
        user = getattr(self.request, "user", None)
        if user and user.is_authenticated and not self.has_permission():
            return redirect("task_manager:telegram-mini-app")
        return super().handle_no_permission()

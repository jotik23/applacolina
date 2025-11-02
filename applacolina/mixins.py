from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
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

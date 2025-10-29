from __future__ import annotations

from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView, LogoutView
from django.urls import reverse_lazy

from .forms import PortalAuthenticationForm


class CalendarPortalView(LoginView):
    template_name = "users/login.html"
    form_class = PortalAuthenticationForm
    redirect_authenticated_user = True

    def get_success_url(self):
        return self.get_redirect_url() or reverse_lazy("calendario:dashboard")


class CalendarLogoutView(LoginRequiredMixin, LogoutView):
    # Explicitly allow GET requests; Django 5 restricts logout to POST by default.
    http_method_names = ["get", "head", "options", "post"]
    next_page = reverse_lazy("portal:login")

    def get(self, request, *args, **kwargs):
        """Allow GET requests to trigger the logout flow."""
        return self.post(request, *args, **kwargs)

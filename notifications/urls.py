from __future__ import annotations

from django.urls import path

from .views import TelegramUpdatesView

app_name = "notifications"

urlpatterns = [
    path("telegram/updates/", TelegramUpdatesView.as_view(), name="telegram-updates"),
]


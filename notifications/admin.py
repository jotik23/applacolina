from __future__ import annotations

from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from . import models


@admin.register(models.TelegramBotConfig)
class TelegramBotConfigAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "default_parse_mode", "webhook_url", "last_synced_at")
    list_filter = ("is_active", "default_parse_mode")
    search_fields = ("name", "description", "token")
    readonly_fields = ("api_base_url", "created_at", "updated_at")
    fieldsets = (
        (
            _("Identificación"),
            {
                "fields": (
                    "name",
                    "description",
                    "token",
                    "is_active",
                )
            },
        ),
        (
            _("Configuración"),
            {
                "fields": (
                    "default_parse_mode",
                    "webhook_url",
                    "allowed_updates",
                    "test_chat_id",
                )
            },
        ),
        (
            _("Auditoría"),
            {"fields": ("api_base_url", "last_synced_at", "created_at", "updated_at")},
        ),
    )


@admin.register(models.TelegramChatLink)
class TelegramChatLinkAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "bot",
        "chat_id",
        "status",
        "verified_at",
        "last_interaction_at",
    )
    list_filter = ("status", "bot")
    search_fields = (
        "user__cedula",
        "user__nombres",
        "user__apellidos",
        "username",
        "chat_id",
    )
    autocomplete_fields = ("bot", "user")
    readonly_fields = ("link_token", "created_at", "updated_at")


@admin.register(models.NotificationTopic)
class NotificationTopicAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "default_channel", "is_active")
    list_filter = ("default_channel", "is_active")
    search_fields = ("name", "slug")


@admin.register(models.NotificationPreference)
class NotificationPreferenceAdmin(admin.ModelAdmin):
    list_display = ("user", "topic", "channel", "is_subscribed", "bot", "farm")
    list_filter = ("channel", "is_subscribed", "bot", "farm", "chicken_house", "position")
    search_fields = (
        "user__cedula",
        "user__nombres",
        "user__apellidos",
        "topic__name",
    )
    autocomplete_fields = ("user", "topic", "bot", "farm", "chicken_house", "room", "position")


@admin.register(models.NotificationTemplate)
class NotificationTemplateAdmin(admin.ModelAdmin):
    list_display = ("topic", "channel", "language_code", "version", "is_default", "is_active")
    list_filter = ("channel", "language_code", "is_default", "is_active")
    search_fields = ("topic__name", "language_code", "body_template")
    autocomplete_fields = ("topic",)


@admin.register(models.NotificationEvent)
class NotificationEventAdmin(admin.ModelAdmin):
    list_display = ("topic", "source_app", "triggered_by", "created_at")
    list_filter = ("source_app", "topic")
    search_fields = ("source_identifier",)
    autocomplete_fields = ("topic", "triggered_by")
    readonly_fields = ("created_at",)


@admin.register(models.NotificationDispatch)
class NotificationDispatchAdmin(admin.ModelAdmin):
    list_display = ("event", "channel", "status", "scheduled_for", "sent_at", "attempt_count")
    list_filter = ("channel", "status", "scheduled_for")
    search_fields = ("event__topic__name", "chat_link__user__nombres", "chat_link__user__apellidos")
    autocomplete_fields = ("event", "preference", "chat_link")
    readonly_fields = ("response_meta", "error_message", "created_at", "updated_at")


@admin.register(models.TelegramInboundUpdate)
class TelegramInboundUpdateAdmin(admin.ModelAdmin):
    list_display = ("bot", "update_id", "received_at", "processed_at")
    list_filter = ("bot",)
    search_fields = ("update_id",)
    autocomplete_fields = ("bot",)
    readonly_fields = ("payload", "received_at", "processed_at", "processing_error")


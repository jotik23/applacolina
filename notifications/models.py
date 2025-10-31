from __future__ import annotations

import uuid
from typing import Any

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from personal.models import PositionDefinition, UserProfile
from production.models import ChickenHouse, Farm, Room


def _default_json_dict() -> dict[str, Any]:
    return {}


class NotificationChannel(models.TextChoices):
    TELEGRAM = "telegram", _("Telegram")
    EMAIL = "email", _("Correo electrónico")
    SMS = "sms", _("SMS")


class TelegramBotConfig(models.Model):
    class ParseMode(models.TextChoices):
        NONE = "", _("Sin formato")
        HTML = "HTML", _("HTML")
        MARKDOWN = "MarkdownV2", _("Markdown V2")

    name = models.CharField(_("Nombre"), max_length=100, unique=True)
    description = models.TextField(_("Descripción"), blank=True)
    token = models.CharField(
        _("Token de acceso"),
        max_length=255,
        help_text=_("Token provisto por BotFather. Se recomienda almacenarlo en un secreto administrado."),
    )
    is_active = models.BooleanField(_("Activo"), default=True)
    default_parse_mode = models.CharField(
        _("Parse mode por defecto"),
        max_length=32,
        choices=ParseMode.choices,
        default=ParseMode.HTML,
        blank=True,
    )
    webhook_url = models.URLField(
        _("Webhook configurado"),
        blank=True,
        help_text=_("URL pública configurada en Telegram para recibir actualizaciones. Opcional."),
    )
    allowed_updates = ArrayField(
        base_field=models.CharField(max_length=50),
        blank=True,
        default=list,
        verbose_name=_("Tipos de actualización permitidos"),
        help_text=_("Lista opcional para restringir los updates entregados por Telegram."),
    )
    test_chat_id = models.BigIntegerField(
        _("Chat de pruebas"),
        null=True,
        blank=True,
        help_text=_("Chat ID usado para pruebas rápidas de conectividad."),
    )
    last_synced_at = models.DateTimeField(
        _("Última sincronización"),
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(_("Creado en"), auto_now_add=True)
    updated_at = models.DateTimeField(_("Actualizado en"), auto_now=True)

    class Meta:
        verbose_name = _("Bot de Telegram")
        verbose_name_plural = _("Bots de Telegram")
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name

    @property
    def api_base_url(self) -> str:
        return f"https://api.telegram.org/bot{self.token}"


class TelegramChatLink(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", _("Pendiente de verificación")
        VERIFIED = "verified", _("Verificado")
        BLOCKED = "blocked", _("Bloqueado por el usuario")
        ARCHIVED = "archived", _("Archivado")

    bot = models.ForeignKey(
        TelegramBotConfig,
        on_delete=models.CASCADE,
        related_name="chat_links",
        verbose_name=_("Bot"),
    )
    user = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name="telegram_chat_links",
        verbose_name=_("Colaborador"),
    )
    telegram_user_id = models.BigIntegerField(_("Usuario de Telegram"))
    chat_id = models.BigIntegerField(_("Chat ID"))
    username = models.CharField(_("Usuario (opcional)"), max_length=150, blank=True)
    first_name = models.CharField(_("Nombre"), max_length=150, blank=True)
    last_name = models.CharField(_("Apellido"), max_length=150, blank=True)
    language_code = models.CharField(_("Idioma"), max_length=12, blank=True)
    link_token = models.UUIDField(_("Token de enlace"), default=uuid.uuid4, editable=False, unique=True)
    link_token_expires_at = models.DateTimeField(
        _("Expira token de enlace"),
        null=True,
        blank=True,
    )
    status = models.CharField(
        _("Estado"),
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
    )
    verified_at = models.DateTimeField(_("Verificado en"), null=True, blank=True)
    last_interaction_at = models.DateTimeField(_("Última interacción"), null=True, blank=True)
    metadata = models.JSONField(_("Metadatos adicionales"), default=_default_json_dict, blank=True)
    created_at = models.DateTimeField(_("Creado en"), auto_now_add=True)
    updated_at = models.DateTimeField(_("Actualizado en"), auto_now=True)

    class Meta:
        verbose_name = _("Vinculación de chat de Telegram")
        verbose_name_plural = _("Vinculaciones de chats de Telegram")
        unique_together = (
            ("bot", "user"),
            ("bot", "chat_id"),
        )
        ordering = ("bot__name", "user__nombres", "user__apellidos")
        indexes = [
            models.Index(fields=("status",)),
            models.Index(fields=("bot", "chat_id")),
        ]

    def __str__(self) -> str:
        return f"{self.user} · {self.bot.name}"

    @property
    def is_verified(self) -> bool:
        return self.status == self.Status.VERIFIED

    def mark_verified(self) -> None:
        self.status = self.Status.VERIFIED
        self.verified_at = timezone.now()
        self.save(update_fields=("status", "verified_at", "updated_at"))


class NotificationTopic(models.Model):
    slug = models.SlugField(_("Identificador"), max_length=100, unique=True)
    name = models.CharField(_("Nombre"), max_length=150)
    description = models.TextField(_("Descripción"), blank=True)
    default_channel = models.CharField(
        _("Canal por defecto"),
        max_length=32,
        choices=NotificationChannel.choices,
        default=NotificationChannel.TELEGRAM,
    )
    is_active = models.BooleanField(_("Activo"), default=True)
    created_at = models.DateTimeField(_("Creado en"), auto_now_add=True)
    updated_at = models.DateTimeField(_("Actualizado en"), auto_now=True)

    class Meta:
        verbose_name = _("Tópico de notificación")
        verbose_name_plural = _("Tópicos de notificación")
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name


class NotificationPreference(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notification_preferences",
        verbose_name=_("Usuario"),
    )
    topic = models.ForeignKey(
        NotificationTopic,
        on_delete=models.CASCADE,
        related_name="preferences",
        verbose_name=_("Tópico"),
    )
    channel = models.CharField(
        _("Canal"),
        max_length=32,
        choices=NotificationChannel.choices,
        default=NotificationChannel.TELEGRAM,
    )
    bot = models.ForeignKey(
        TelegramBotConfig,
        on_delete=models.SET_NULL,
        related_name="preferences",
        verbose_name=_("Bot preferido"),
        null=True,
        blank=True,
        help_text=_("Opcional: seleccionar un bot específico cuando se soporten múltiples."),
    )
    is_subscribed = models.BooleanField(_("Suscrito"), default=True)
    farm = models.ForeignKey(
        Farm,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notification_preferences",
        verbose_name=_("Granja"),
    )
    chicken_house = models.ForeignKey(
        ChickenHouse,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notification_preferences",
        verbose_name=_("Galpón"),
    )
    room = models.ForeignKey(
        Room,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notification_preferences",
        verbose_name=_("Salón"),
    )
    position = models.ForeignKey(
        PositionDefinition,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notification_preferences",
        verbose_name=_("Posición"),
    )
    quiet_hours_start = models.TimeField(_("Inicio silencio"), null=True, blank=True)
    quiet_hours_end = models.TimeField(_("Fin silencio"), null=True, blank=True)
    created_at = models.DateTimeField(_("Creado en"), auto_now_add=True)
    updated_at = models.DateTimeField(_("Actualizado en"), auto_now=True)

    class Meta:
        verbose_name = _("Preferencia de notificación")
        verbose_name_plural = _("Preferencias de notificación")
        ordering = ("user__nombres", "topic__name")
        constraints = [
            models.UniqueConstraint(
                fields=(
                    "user",
                    "topic",
                    "channel",
                    "bot",
                    "farm",
                    "chicken_house",
                    "room",
                    "position",
                ),
                name="notifications_unique_preference_context",
            )
        ]

    def __str__(self) -> str:
        return f"{self.user} · {self.topic} ({self.channel})"


class NotificationTemplate(models.Model):
    topic = models.ForeignKey(
        NotificationTopic,
        on_delete=models.CASCADE,
        related_name="templates",
        verbose_name=_("Tópico"),
    )
    channel = models.CharField(
        _("Canal"),
        max_length=32,
        choices=NotificationChannel.choices,
        default=NotificationChannel.TELEGRAM,
    )
    language_code = models.CharField(
        _("Idioma"),
        max_length=12,
        default="es-CO",
        help_text=_("Código de idioma según BCP 47, por ejemplo es-CO."),
    )
    version = models.PositiveIntegerField(
        _("Versión"),
        default=1,
        validators=[MinValueValidator(1), MaxValueValidator(999)],
    )
    title = models.CharField(_("Título"), max_length=150, blank=True)
    body_template = models.TextField(
        _("Cuerpo del mensaje"),
        help_text=_("Plantilla compatible con Django Template para renderizar el mensaje final."),
    )
    keyboard = models.JSONField(
        _("Keyboard"),
        default=_default_json_dict,
        blank=True,
        help_text=_("Opcional: estructura de botones inline o teclado personalizado."),
    )
    extra_options = models.JSONField(
        _("Opciones adicionales"),
        default=_default_json_dict,
        blank=True,
        help_text=_("Parámetros adicionales para el envío al canal seleccionado."),
    )
    parse_mode = models.CharField(
        _("Parse mode"),
        max_length=32,
        choices=TelegramBotConfig.ParseMode.choices,
        blank=True,
    )
    is_default = models.BooleanField(
        _("Es plantilla por defecto"),
        default=False,
        help_text=_("Se utiliza cuando no se especifica versión explícita para el canal/idioma."),
    )
    is_active = models.BooleanField(_("Activo"), default=True)
    created_at = models.DateTimeField(_("Creado en"), auto_now_add=True)
    updated_at = models.DateTimeField(_("Actualizado en"), auto_now=True)

    class Meta:
        verbose_name = _("Plantilla de notificación")
        verbose_name_plural = _("Plantillas de notificación")
        ordering = ("topic__name", "channel", "language_code", "version")
        constraints = [
            models.UniqueConstraint(
                fields=("topic", "channel", "language_code", "version"),
                name="notifications_unique_template_version",
            )
        ]

    def __str__(self) -> str:
        return f"{self.topic} · {self.channel} · {self.language_code} v{self.version}"


class NotificationEvent(models.Model):
    class SourceApp(models.TextChoices):
        CALENDAR = "calendar", _("Calendario")
        TASK_MANAGER = "task_manager", _("Gestor de tareas")
        PERSONAL = "personal", _("Personal")
        PRODUCTION = "production", _("Producción")
        MANUAL = "manual", _("Manual")
        OTHER = "other", _("Otro")

    topic = models.ForeignKey(
        NotificationTopic,
        on_delete=models.PROTECT,
        related_name="events",
        verbose_name=_("Tópico"),
    )
    triggered_by = models.ForeignKey(
        UserProfile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="triggered_notifications",
        verbose_name=_("Generado por"),
    )
    source_app = models.CharField(
        _("Origen"),
        max_length=32,
        choices=SourceApp.choices,
        default=SourceApp.OTHER,
    )
    source_identifier = models.CharField(
        _("Identificador origen"),
        max_length=100,
        blank=True,
        help_text=_("Identificador libre del recurso que generó el evento."),
    )
    context = models.JSONField(
        _("Contexto"),
        default=_default_json_dict,
        blank=True,
        help_text=_("Payload normalizado con información necesaria para renderizar la plantilla."),
    )
    created_at = models.DateTimeField(_("Creado en"), auto_now_add=True)

    class Meta:
        verbose_name = _("Evento de notificación")
        verbose_name_plural = _("Eventos de notificación")
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("topic", "source_app")),
        ]

    def __str__(self) -> str:
        return f"{self.topic} · {self.created_at:%Y-%m-%d %H:%M}"


class NotificationDispatch(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", _("Pendiente")
        PROCESSING = "processing", _("En proceso")
        SENT = "sent", _("Enviado")
        FAILED = "failed", _("Fallido")
        SKIPPED = "skipped", _("Omitido")
        CANCELLED = "cancelled", _("Cancelado")

    event = models.ForeignKey(
        NotificationEvent,
        on_delete=models.CASCADE,
        related_name="dispatches",
        verbose_name=_("Evento"),
    )
    preference = models.ForeignKey(
        NotificationPreference,
        on_delete=models.SET_NULL,
        related_name="dispatches",
        verbose_name=_("Preferencia utilizada"),
        null=True,
        blank=True,
    )
    chat_link = models.ForeignKey(
        TelegramChatLink,
        on_delete=models.SET_NULL,
        related_name="dispatches",
        verbose_name=_("Chat de destino"),
        null=True,
        blank=True,
    )
    channel = models.CharField(
        _("Canal"),
        max_length=32,
        choices=NotificationChannel.choices,
        default=NotificationChannel.TELEGRAM,
    )
    payload = models.JSONField(
        _("Payload de mensaje"),
        default=_default_json_dict,
        blank=True,
        help_text=_("Datos ya renderizados listos para enviar al canal."),
    )
    status = models.CharField(
        _("Estado"),
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
    )
    scheduled_for = models.DateTimeField(
        _("Programado para"),
        null=True,
        blank=True,
    )
    sent_at = models.DateTimeField(_("Enviado en"), null=True, blank=True)
    error_message = models.TextField(_("Error"), blank=True)
    response_meta = models.JSONField(_("Respuesta canal"), default=_default_json_dict, blank=True)
    attempt_count = models.PositiveIntegerField(_("Intentos"), default=0)
    created_at = models.DateTimeField(_("Creado en"), auto_now_add=True)
    updated_at = models.DateTimeField(_("Actualizado en"), auto_now=True)

    class Meta:
        verbose_name = _("Despacho de notificación")
        verbose_name_plural = _("Despachos de notificaciones")
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("status", "scheduled_for")),
            models.Index(fields=("channel", "status")),
        ]

    def __str__(self) -> str:
        return f"{self.get_channel_display()} · {self.get_status_display()}"

    def mark_processing(self) -> None:
        if self.status != self.Status.PROCESSING:
            self.status = self.Status.PROCESSING
            self.attempt_count += 1
            self.updated_at = timezone.now()
            self.save(update_fields=("status", "attempt_count", "updated_at"))

    def mark_sent(self, response: dict[str, Any]) -> None:
        self.status = self.Status.SENT
        self.response_meta = response
        self.sent_at = timezone.now()
        self.error_message = ""
        self.save(update_fields=("status", "response_meta", "sent_at", "error_message", "updated_at"))

    def mark_failed(self, error_message: str, response: dict[str, Any] | None = None) -> None:
        self.status = self.Status.FAILED
        if response is not None:
            self.response_meta = response
        self.error_message = error_message
        self.updated_at = timezone.now()
        self.save(update_fields=("status", "response_meta", "error_message", "updated_at"))


class TelegramInboundUpdate(models.Model):
    bot = models.ForeignKey(
        TelegramBotConfig,
        on_delete=models.CASCADE,
        related_name="inbound_updates",
        verbose_name=_("Bot"),
    )
    update_id = models.BigIntegerField(_("Update ID"))
    payload = models.JSONField(_("Payload bruto"))
    received_at = models.DateTimeField(_("Recibido en"), auto_now_add=True)
    processed_at = models.DateTimeField(_("Procesado en"), null=True, blank=True)
    processing_error = models.TextField(_("Error de procesamiento"), blank=True)

    class Meta:
        verbose_name = _("Update entrante de Telegram")
        verbose_name_plural = _("Updates entrantes de Telegram")
        ordering = ("-received_at",)
        constraints = [
            models.UniqueConstraint(fields=("bot", "update_id"), name="notifications_unique_update_id"),
        ]

    def __str__(self) -> str:
        return f"{self.bot.name} · {self.update_id}"


from __future__ import annotations

import django.contrib.postgres.fields
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("personal", "0020_usergroup"),
        ("production", "0003_rename_granjas_tables"),
    ]

    operations = [
        migrations.CreateModel(
            name="TelegramBotConfig",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("name", models.CharField(max_length=100, unique=True, verbose_name="Nombre")),
                ("description", models.TextField(blank=True, verbose_name="Descripción")),
                (
                    "token",
                    models.CharField(
                        help_text="Token provisto por BotFather. Se recomienda almacenarlo en un secreto administrado.",
                        max_length=255,
                        verbose_name="Token de acceso",
                    ),
                ),
                ("is_active", models.BooleanField(default=True, verbose_name="Activo")),
                (
                    "default_parse_mode",
                    models.CharField(
                        blank=True,
                        choices=[("", "Sin formato"), ("HTML", "HTML"), ("MarkdownV2", "Markdown V2")],
                        default="HTML",
                        max_length=32,
                        verbose_name="Parse mode por defecto",
                    ),
                ),
                (
                    "webhook_url",
                    models.URLField(
                        blank=True,
                        help_text="URL pública configurada en Telegram para recibir actualizaciones. Opcional.",
                        verbose_name="Webhook configurado",
                    ),
                ),
                (
                    "allowed_updates",
                    django.contrib.postgres.fields.ArrayField(
                        base_field=models.CharField(max_length=50),
                        blank=True,
                        default=list,
                        help_text="Lista opcional para restringir los updates entregados por Telegram.",
                        size=None,
                        verbose_name="Tipos de actualización permitidos",
                    ),
                ),
                (
                    "test_chat_id",
                    models.BigIntegerField(
                        blank=True,
                        help_text="Chat ID usado para pruebas rápidas de conectividad.",
                        null=True,
                        verbose_name="Chat de pruebas",
                    ),
                ),
                ("last_synced_at", models.DateTimeField(blank=True, null=True, verbose_name="Última sincronización")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Creado en")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Actualizado en")),
            ],
            options={
                "verbose_name": "Bot de Telegram",
                "verbose_name_plural": "Bots de Telegram",
                "ordering": ("name",),
            },
        ),
        migrations.CreateModel(
            name="NotificationTopic",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("slug", models.SlugField(max_length=100, unique=True, verbose_name="Identificador")),
                ("name", models.CharField(max_length=150, verbose_name="Nombre")),
                ("description", models.TextField(blank=True, verbose_name="Descripción")),
                (
                    "default_channel",
                    models.CharField(
                        choices=[
                            ("telegram", "Telegram"),
                            ("email", "Correo electrónico"),
                            ("sms", "SMS"),
                        ],
                        default="telegram",
                        max_length=32,
                        verbose_name="Canal por defecto",
                    ),
                ),
                ("is_active", models.BooleanField(default=True, verbose_name="Activo")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Creado en")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Actualizado en")),
            ],
            options={
                "verbose_name": "Tópico de notificación",
                "verbose_name_plural": "Tópicos de notificación",
                "ordering": ("name",),
            },
        ),
        migrations.CreateModel(
            name="TelegramChatLink",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("telegram_user_id", models.BigIntegerField(verbose_name="Usuario de Telegram")),
                ("chat_id", models.BigIntegerField(verbose_name="Chat ID")),
                ("username", models.CharField(blank=True, max_length=150, verbose_name="Usuario (opcional)")),
                ("first_name", models.CharField(blank=True, max_length=150, verbose_name="Nombre")),
                ("last_name", models.CharField(blank=True, max_length=150, verbose_name="Apellido")),
                ("language_code", models.CharField(blank=True, max_length=12, verbose_name="Idioma")),
                (
                    "link_token",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        unique=True,
                        verbose_name="Token de enlace",
                    ),
                ),
                (
                    "link_token_expires_at",
                    models.DateTimeField(blank=True, null=True, verbose_name="Expira token de enlace"),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pendiente de verificación"),
                            ("verified", "Verificado"),
                            ("blocked", "Bloqueado por el usuario"),
                            ("archived", "Archivado"),
                        ],
                        default="pending",
                        max_length=16,
                        verbose_name="Estado",
                    ),
                ),
                ("verified_at", models.DateTimeField(blank=True, null=True, verbose_name="Verificado en")),
                ("last_interaction_at", models.DateTimeField(blank=True, null=True, verbose_name="Última interacción")),
                ("metadata", models.JSONField(blank=True, default=dict, verbose_name="Metadatos adicionales")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Creado en")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Actualizado en")),
                (
                    "bot",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="chat_links",
                        to="notifications.telegrambotconfig",
                        verbose_name="Bot",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="telegram_chat_links",
                        to="personal.userprofile",
                        verbose_name="Colaborador",
                    ),
                ),
            ],
            options={
                "verbose_name": "Vinculación de chat de Telegram",
                "verbose_name_plural": "Vinculaciones de chats de Telegram",
                "ordering": ("bot__name", "user__nombres", "user__apellidos"),
            },
        ),
        migrations.CreateModel(
            name="NotificationTemplate",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "channel",
                    models.CharField(
                        choices=[
                            ("telegram", "Telegram"),
                            ("email", "Correo electrónico"),
                            ("sms", "SMS"),
                        ],
                        default="telegram",
                        max_length=32,
                        verbose_name="Canal",
                    ),
                ),
                (
                    "language_code",
                    models.CharField(
                        default="es-CO",
                        help_text="Código de idioma según BCP 47, por ejemplo es-CO.",
                        max_length=12,
                        verbose_name="Idioma",
                    ),
                ),
                (
                    "version",
                    models.PositiveIntegerField(
                        default=1,
                        validators=[MinValueValidator(1), MaxValueValidator(999)],
                        verbose_name="Versión",
                    ),
                ),
                ("title", models.CharField(blank=True, max_length=150, verbose_name="Título")),
                (
                    "body_template",
                    models.TextField(
                        help_text="Plantilla compatible con Django Template para renderizar el mensaje final.",
                        verbose_name="Cuerpo del mensaje",
                    ),
                ),
                (
                    "keyboard",
                    models.JSONField(
                        blank=True,
                        default=dict,
                        help_text="Opcional: estructura de botones inline o teclado personalizado.",
                        verbose_name="Keyboard",
                    ),
                ),
                (
                    "extra_options",
                    models.JSONField(
                        blank=True,
                        default=dict,
                        help_text="Parámetros adicionales para el envío al canal seleccionado.",
                        verbose_name="Opciones adicionales",
                    ),
                ),
                (
                    "parse_mode",
                    models.CharField(
                        blank=True,
                        choices=[("", "Sin formato"), ("HTML", "HTML"), ("MarkdownV2", "Markdown V2")],
                        max_length=32,
                        verbose_name="Parse mode",
                    ),
                ),
                (
                    "is_default",
                    models.BooleanField(
                        default=False,
                        help_text="Se utiliza cuando no se especifica versión explícita para el canal/idioma.",
                        verbose_name="Es plantilla por defecto",
                    ),
                ),
                ("is_active", models.BooleanField(default=True, verbose_name="Activo")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Creado en")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Actualizado en")),
                (
                    "topic",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="templates",
                        to="notifications.notificationtopic",
                        verbose_name="Tópico",
                    ),
                ),
            ],
            options={
                "verbose_name": "Plantilla de notificación",
                "verbose_name_plural": "Plantillas de notificación",
                "ordering": ("topic__name", "channel", "language_code", "version"),
            },
        ),
        migrations.CreateModel(
            name="NotificationEvent",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "source_app",
                    models.CharField(
                        choices=[
                            ("calendar", "Calendario"),
                            ("task_manager", "Gestor de tareas"),
                            ("personal", "Personal"),
                            ("production", "Producción"),
                            ("manual", "Manual"),
                            ("other", "Otro"),
                        ],
                        default="other",
                        max_length=32,
                        verbose_name="Origen",
                    ),
                ),
                (
                    "source_identifier",
                    models.CharField(
                        blank=True,
                        help_text="Identificador libre del recurso que generó el evento.",
                        max_length=100,
                        verbose_name="Identificador origen",
                    ),
                ),
                (
                    "context",
                    models.JSONField(
                        blank=True,
                        default=dict,
                        help_text="Payload normalizado con información necesaria para renderizar la plantilla.",
                        verbose_name="Contexto",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Creado en")),
                (
                    "topic",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="events",
                        to="notifications.notificationtopic",
                        verbose_name="Tópico",
                    ),
                ),
                (
                    "triggered_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="triggered_notifications",
                        to="personal.userprofile",
                        verbose_name="Generado por",
                    ),
                ),
            ],
            options={
                "verbose_name": "Evento de notificación",
                "verbose_name_plural": "Eventos de notificación",
                "ordering": ("-created_at",),
            },
        ),
        migrations.CreateModel(
            name="NotificationPreference",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "channel",
                    models.CharField(
                        choices=[
                            ("telegram", "Telegram"),
                            ("email", "Correo electrónico"),
                            ("sms", "SMS"),
                        ],
                        default="telegram",
                        max_length=32,
                        verbose_name="Canal",
                    ),
                ),
                ("is_subscribed", models.BooleanField(default=True, verbose_name="Suscrito")),
                ("quiet_hours_start", models.TimeField(blank=True, null=True, verbose_name="Inicio silencio")),
                ("quiet_hours_end", models.TimeField(blank=True, null=True, verbose_name="Fin silencio")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Creado en")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Actualizado en")),
                (
                    "bot",
                    models.ForeignKey(
                        blank=True,
                        help_text="Opcional: seleccionar un bot específico cuando se soporten múltiples.",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="preferences",
                        to="notifications.telegrambotconfig",
                        verbose_name="Bot preferido",
                    ),
                ),
                (
                    "chicken_house",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="notification_preferences",
                        to="production.chickenhouse",
                        verbose_name="Galpón",
                    ),
                ),
                (
                    "farm",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="notification_preferences",
                        to="production.farm",
                        verbose_name="Granja",
                    ),
                ),
                (
                    "position",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="notification_preferences",
                        to="personal.positiondefinition",
                        verbose_name="Posición",
                    ),
                ),
                (
                    "room",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="notification_preferences",
                        to="production.room",
                        verbose_name="Salón",
                    ),
                ),
                (
                    "topic",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="preferences",
                        to="notifications.notificationtopic",
                        verbose_name="Tópico",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="notification_preferences",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Usuario",
                    ),
                ),
            ],
            options={
                "verbose_name": "Preferencia de notificación",
                "verbose_name_plural": "Preferencias de notificación",
                "ordering": ("user__nombres", "topic__name"),
            },
        ),
        migrations.CreateModel(
            name="TelegramInboundUpdate",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("update_id", models.BigIntegerField(verbose_name="Update ID")),
                ("payload", models.JSONField(verbose_name="Payload bruto")),
                ("received_at", models.DateTimeField(auto_now_add=True, verbose_name="Recibido en")),
                ("processed_at", models.DateTimeField(blank=True, null=True, verbose_name="Procesado en")),
                ("processing_error", models.TextField(blank=True, verbose_name="Error de procesamiento")),
                (
                    "bot",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="inbound_updates",
                        to="notifications.telegrambotconfig",
                        verbose_name="Bot",
                    ),
                ),
            ],
            options={
                "verbose_name": "Update entrante de Telegram",
                "verbose_name_plural": "Updates entrantes de Telegram",
                "ordering": ("-received_at",),
            },
        ),
        migrations.CreateModel(
            name="NotificationDispatch",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "channel",
                    models.CharField(
                        choices=[
                            ("telegram", "Telegram"),
                            ("email", "Correo electrónico"),
                            ("sms", "SMS"),
                        ],
                        default="telegram",
                        max_length=32,
                        verbose_name="Canal",
                    ),
                ),
                (
                    "payload",
                    models.JSONField(
                        blank=True,
                        default=dict,
                        help_text="Datos ya renderizados listos para enviar al canal.",
                        verbose_name="Payload de mensaje",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pendiente"),
                            ("processing", "En proceso"),
                            ("sent", "Enviado"),
                            ("failed", "Fallido"),
                            ("skipped", "Omitido"),
                            ("cancelled", "Cancelado"),
                        ],
                        default="pending",
                        max_length=16,
                        verbose_name="Estado",
                    ),
                ),
                ("scheduled_for", models.DateTimeField(blank=True, null=True, verbose_name="Programado para")),
                ("sent_at", models.DateTimeField(blank=True, null=True, verbose_name="Enviado en")),
                ("error_message", models.TextField(blank=True, verbose_name="Error")),
                (
                    "response_meta",
                    models.JSONField(blank=True, default=dict, verbose_name="Respuesta canal"),
                ),
                ("attempt_count", models.PositiveIntegerField(default=0, verbose_name="Intentos")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Creado en")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Actualizado en")),
                (
                    "chat_link",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="dispatches",
                        to="notifications.telegramchatlink",
                        verbose_name="Chat de destino",
                    ),
                ),
                (
                    "event",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="dispatches",
                        to="notifications.notificationevent",
                        verbose_name="Evento",
                    ),
                ),
                (
                    "preference",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="dispatches",
                        to="notifications.notificationpreference",
                        verbose_name="Preferencia utilizada",
                    ),
                ),
            ],
            options={
                "verbose_name": "Despacho de notificación",
                "verbose_name_plural": "Despachos de notificaciones",
                "ordering": ("-created_at",),
            },
        ),
        migrations.AddIndex(
            model_name="notificationdispatch",
            index=models.Index(fields=["status", "scheduled_for"], name="notification_status_cf6188_idx"),
        ),
        migrations.AddIndex(
            model_name="notificationdispatch",
            index=models.Index(fields=["channel", "status"], name="notification_channel_2b838c_idx"),
        ),
        migrations.AddConstraint(
            model_name="notificationpreference",
            constraint=models.UniqueConstraint(
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
            ),
        ),
        migrations.AddIndex(
            model_name="notificationevent",
            index=models.Index(fields=["topic", "source_app"], name="notification_topic__6d808b_idx"),
        ),
        migrations.AddIndex(
            model_name="telegramchatlink",
            index=models.Index(fields=["status"], name="notification_status_83b4c7_idx"),
        ),
        migrations.AddIndex(
            model_name="telegramchatlink",
            index=models.Index(fields=["bot", "chat_id"], name="notification_bot_id_ae5099_idx"),
        ),
        migrations.AlterUniqueTogether(
            name="telegramchatlink",
            unique_together={("bot", "chat_id"), ("bot", "user")},
        ),
        migrations.AddConstraint(
            model_name="telegraminboundupdate",
            constraint=models.UniqueConstraint(
                fields=("bot", "update_id"),
                name="notifications_unique_update_id",
            ),
        ),
        migrations.AddConstraint(
            model_name="notificationtemplate",
            constraint=models.UniqueConstraint(
                fields=("topic", "channel", "language_code", "version"),
                name="notifications_unique_template_version",
            ),
        ),
    ]

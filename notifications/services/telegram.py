from __future__ import annotations

from typing import Any

import httpx
from django.utils import timezone

from ..models import (
    NotificationChannel,
    NotificationDispatch,
    TelegramBotConfig,
    TelegramChatLink,
)


class TelegramNotificationError(Exception):
    """Excepción cuando la API de Telegram retorna un error."""

    def __init__(self, message: str, *, response: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.response = response or {}


class TelegramAPIClient:
    """Cliente ligero para consumir la API de Telegram."""

    def __init__(self, bot: TelegramBotConfig, *, timeout: float = 10.0) -> None:
        self.bot = bot
        self.timeout = timeout

    def _request(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.bot.api_base_url}/{method}"
        response = httpx.post(url, json=payload, timeout=self.timeout)
        response.raise_for_status()
        data = response.json()
        if not data.get("ok", False):
            raise TelegramNotificationError(
                data.get("description") or "Telegram API error.",
                response=data,
            )
        return data

    def send_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Envía un mensaje usando sendMessage."""
        return self._request("sendMessage", payload)


class TelegramNotificationSender:
    """Servicio encargado de enviar despachos individuales a Telegram."""

    def __init__(self, *, timeout: float = 10.0) -> None:
        self.timeout = timeout

    def send_dispatch(self, dispatch: NotificationDispatch) -> dict[str, Any]:
        if dispatch.channel != NotificationChannel.TELEGRAM:
            raise TelegramNotificationError("El despacho no corresponde al canal de Telegram.")

        chat_link = dispatch.chat_link
        if chat_link is None:
            raise TelegramNotificationError("El despacho no tiene chat enlazado.")

        if not chat_link.is_verified:
            raise TelegramNotificationError("El chat de Telegram no está verificado.")

        payload = dict(dispatch.payload or {})
        text = payload.get("text")
        if not text:
            raise TelegramNotificationError("El payload del despacho no contiene 'text'.")

        message_payload: dict[str, Any] = {
            "chat_id": chat_link.chat_id,
            "text": text,
        }

        parse_mode = payload.get("parse_mode") or chat_link.bot.default_parse_mode
        if parse_mode:
            message_payload["parse_mode"] = parse_mode

        optional_fields = (
            "disable_notification",
            "protect_content",
            "reply_markup",
            "link_preview_options",
            "entities",
            "message_thread_id",
        )
        for field in optional_fields:
            if field in payload:
                message_payload[field] = payload[field]

        extra = payload.get("extra") or {}
        message_payload.update(extra)

        dispatch.mark_processing()
        client = TelegramAPIClient(chat_link.bot, timeout=self.timeout)
        try:
            response = client.send_message(message_payload)
        except httpx.HTTPError as exc:
            dispatch.mark_failed(str(exc))
            raise TelegramNotificationError("Error de transporte al enviar la notificación.") from exc
        except TelegramNotificationError as exc:
            dispatch.mark_failed(str(exc), response=exc.response)
            raise
        else:
            dispatch.mark_sent(response)
            chat_link.last_interaction_at = timezone.now()
            chat_link.save(update_fields=("last_interaction_at", "updated_at"))
            return response

    def __call__(self, dispatch: NotificationDispatch) -> dict[str, Any]:
        return self.send_dispatch(dispatch)


from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from django.conf import settings

from personal.models import UserProfile
from pywebpush import WebPushException, webpush

from task_manager.models import MiniAppPushSubscription

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PushNotificationAction:
    label: str
    url: str


@dataclass(slots=True)
class PushNotificationMessage:
    title: str
    body: str
    data: dict[str, Any] = field(default_factory=dict)
    icon_url: str | None = None
    badge_url: str | None = None
    tag: str | None = None
    require_interaction: bool = False
    action: PushNotificationAction | None = None

    def as_payload(self) -> dict[str, Any]:
        payload_data = dict(self.data)
        if self.action:
            payload_data.setdefault("cta_label", self.action.label)
            payload_data.setdefault("url", self.action.url)
        payload: dict[str, Any] = {
            "title": self.title,
            "body": self.body,
            "data": payload_data,
            "requireInteraction": self.require_interaction,
        }
        if self.icon_url:
            payload["icon"] = self.icon_url
        if self.badge_url:
            payload["badge"] = self.badge_url
        if self.tag:
            payload["tag"] = self.tag
        return payload


@dataclass(slots=True)
class PushNotificationResult:
    attempted: int
    delivered: int
    failures: list[str]
    skipped_reason: str | None = None

    @property
    def success(self) -> bool:
        return self.delivered > 0 and not self.failures


class PushNotificationService:
    """Lightweight wrapper around pywebpush for Mini App notifications."""

    def __init__(
        self,
        *,
        vapid_private_key: str | None = None,
        vapid_contact: str | None = None,
        subscription_queryset: Callable[[], Iterable[MiniAppPushSubscription]] | None = None,
        webpush_client: Callable[..., Any] | None = None,
    ) -> None:
        self._vapid_private_key = vapid_private_key or getattr(settings, "WEB_PUSH_PRIVATE_KEY", "")
        self._vapid_contact = vapid_contact or getattr(settings, "WEB_PUSH_CONTACT", "") or "mailto:soporte@lacolina.com"
        self._webpush_client = webpush_client or webpush
        self._subscription_queryset = subscription_queryset

    def is_enabled(self) -> bool:
        return bool(self._vapid_private_key)

    def _get_subscriptions_for_user(self, user: UserProfile) -> list[MiniAppPushSubscription]:
        if self._subscription_queryset:
            candidates = self._subscription_queryset()
            return [subscription for subscription in candidates if subscription.user_id == user.pk and subscription.is_active]
        return list(
            MiniAppPushSubscription.objects.filter(user=user, is_active=True).order_by("-updated_at")
        )

    def send_to_user(
        self,
        *,
        user: UserProfile,
        message: PushNotificationMessage,
        notification_type: str,
        ttl: int = 300,
    ) -> PushNotificationResult:
        if not self.is_enabled():
            logger.info(
                "Skipping push notification '%s' because WEB_PUSH_PRIVATE_KEY is not configured.",
                notification_type,
            )
            return PushNotificationResult(attempted=0, delivered=0, failures=[], skipped_reason="disabled")

        subscriptions = self._get_subscriptions_for_user(user)
        if not subscriptions:
            logger.debug(
                "No push subscriptions found for user %s when sending '%s'.",
                user.pk,
                notification_type,
            )
            return PushNotificationResult(
                attempted=0,
                delivered=0,
                failures=[],
                skipped_reason="no-subscriptions",
            )

        payload = json.dumps(message.as_payload())
        failures: list[str] = []
        delivered = 0
        for subscription in subscriptions:
            subscription_info = {
                "endpoint": subscription.endpoint,
                "keys": {
                    "p256dh": subscription.p256dh_key,
                    "auth": subscription.auth_key,
                },
            }
            try:
                self._webpush_client(
                    subscription_info=subscription_info,
                    data=payload,
                    vapid_private_key=self._vapid_private_key,
                    vapid_claims={"sub": self._vapid_contact},
                    ttl=ttl,
                )
                delivered += 1
            except WebPushException as exc:  # pragma: no cover - network errors are environment-specific
                detail = self._format_error(exc)
                failures.append(detail)
                logger.warning(
                    "Unable to deliver '%s' to subscription %s: %s",
                    notification_type,
                    subscription.pk,
                    detail,
                    exc_info=exc,
                )
                if self._should_disable_subscription(exc):
                    subscription.mark_inactive()

        return PushNotificationResult(
            attempted=len(subscriptions),
            delivered=delivered,
            failures=failures,
        )

    @staticmethod
    def _format_error(exc: WebPushException) -> str:
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)
        text = ""
        if response is not None:
            try:
                text = response.text  # type: ignore[assignment]
            except Exception:  # noqa: BLE001
                text = ""
        return f"{exc} (status={status_code}) {text}".strip()

    @staticmethod
    def _should_disable_subscription(exc: WebPushException) -> bool:
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)
        return status_code in {404, 410}

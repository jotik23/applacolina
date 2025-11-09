from __future__ import annotations

import json

from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from personal.models import UserProfile
from task_manager.models import MiniAppPushSubscription


class MiniAppPushSubscriptionViewTests(TestCase):
    def setUp(self):
        self.url = reverse("task_manager:mini-app-pwa-subscriptions")
        self.user = UserProfile.objects.create_user(
            cedula="900001",
            password=None,
            nombres="Operario",
            apellidos="Push",
        )
        self.access_permission = Permission.objects.get(codename="access_mini_app")

    def _post(self, payload: dict[str, object], *, user: UserProfile | None = None):
        if user:
            self.client.force_login(user)
        return self.client.post(
            self.url,
            data=json.dumps(payload),
            content_type="application/json",
        )

    def _build_payload(self) -> dict[str, object]:
        return {
            "subscription": {
                "endpoint": "https://fcm.googleapis.com/fcm/send/example-token",
                "keys": {"p256dh": "abc123", "auth": "xyz987"},
                "expirationTime": None,
            }
        }

    def test_requires_authentication(self):
        response = self._post(self._build_payload())
        self.assertEqual(response.status_code, 401)

    def test_requires_access_permission(self):
        response = self._post(self._build_payload(), user=self.user)
        self.assertEqual(response.status_code, 403)

    def test_creates_subscription_for_user(self):
        self.user.user_permissions.add(self.access_permission)
        response = self._post(self._build_payload(), user=self.user)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(MiniAppPushSubscription.objects.count(), 1)
        subscription = MiniAppPushSubscription.objects.get()
        self.assertEqual(subscription.user, self.user)
        self.assertEqual(subscription.endpoint, "https://fcm.googleapis.com/fcm/send/example-token")
        self.assertEqual(subscription.p256dh_key, "abc123")
        self.assertEqual(subscription.auth_key, "xyz987")

    def test_reusing_endpoint_updates_existing_record(self):
        self.user.user_permissions.add(self.access_permission)
        first_response = self._post(self._build_payload(), user=self.user)
        self.assertEqual(first_response.status_code, 201)

        payload = self._build_payload()
        payload["subscription"]["keys"]["p256dh"] = "newkey"  # type: ignore[index]
        second_response = self._post(payload, user=self.user)
        self.assertEqual(second_response.status_code, 200)

        self.assertEqual(MiniAppPushSubscription.objects.count(), 1)
        subscription = MiniAppPushSubscription.objects.get()
        self.assertEqual(subscription.p256dh_key, "newkey")

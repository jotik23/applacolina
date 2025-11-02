from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from personal.models import UserProfile


class MiniAppSuggestionsCardTests(TestCase):
    def setUp(self):
        self.access_permission = Permission.objects.get(codename="access_mini_app")
        self.suggestions_permission = Permission.objects.get(codename="view_mini_app_suggestions_card")

    def _create_user(self, *, with_suggestions_permission: bool) -> UserProfile:
        document = "900123450" if with_suggestions_permission else "900123451"
        user = UserProfile.objects.create_user(
            document,
            password=None,
            nombres="Laura",
            apellidos="Ramírez",
            telefono="3105558888",
        )
        user.user_permissions.add(self.access_permission)
        if with_suggestions_permission:
            user.user_permissions.add(self.suggestions_permission)
        return user

    def test_card_visible_with_permission(self):
        user = self._create_user(with_suggestions_permission=True)
        self.client.force_login(user)

        response = self.client.get(reverse("task_manager:telegram-mini-app"))
        self.assertEqual(response.status_code, 200)

        card_permissions = response.context["mini_app_card_permissions"]
        self.assertTrue(card_permissions["suggestions"])

        self.assertContains(response, 'data-suggestions-card')
        self.assertContains(response, 'data-report-source="suggestions-card"')

    def test_card_hidden_without_permission(self):
        user = self._create_user(with_suggestions_permission=False)
        self.client.force_login(user)

        response = self.client.get(reverse("task_manager:telegram-mini-app"))
        self.assertEqual(response.status_code, 200)

        card_permissions = response.context["mini_app_card_permissions"]
        self.assertFalse(card_permissions["suggestions"])

        self.assertNotContains(response, 'data-suggestions-card')


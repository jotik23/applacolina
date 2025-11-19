from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from personal.models import UserProfile


class MiniAppEggStagePermissionTests(TestCase):
    def setUp(self) -> None:
        self.user = UserProfile.objects.create_user(
            "1002999000",
            password=None,
            nombres="Camilo",
            apellidos="Vargas",
            telefono="3010000000",
        )
        access_perm = Permission.objects.get(codename="access_mini_app")
        self.user.user_permissions.add(access_perm)

    def _login(self) -> None:
        self.client.force_login(self.user)

    def test_stage_cards_hidden_without_permissions(self) -> None:
        self._login()
        response = self.client.get(reverse("task_manager:telegram-mini-app"))
        self.assertEqual(response.status_code, 200)
        card_permissions = response.context["mini_app_card_permissions"]
        self.assertFalse(card_permissions["egg_stage_transport"])
        egg_workflow = response.context["telegram_mini_app"]["egg_workflow"]
        self.assertEqual(egg_workflow["stages"], [])

    def test_stage_cards_visible_with_legacy_permission(self) -> None:
        legacy_perm = Permission.objects.get(codename="view_mini_app_egg_stage_cards")
        self.user.user_permissions.add(legacy_perm)
        self._login()

        response = self.client.get(reverse("task_manager:telegram-mini-app"))
        self.assertEqual(response.status_code, 200)
        card_permissions = response.context["mini_app_card_permissions"]
        for key in (
            "egg_stage_transport",
            "egg_stage_verification",
            "egg_stage_classification",
            "egg_stage_inspection",
            "egg_stage_inventory",
            "egg_stage_dispatches",
        ):
            self.assertTrue(card_permissions[key], msg=f"{key} permission must be granted via legacy flag")
        egg_workflow = response.context["telegram_mini_app"]["egg_workflow"]
        stage_ids = [stage["id"] for stage in egg_workflow["stages"]]
        self.assertEqual(
            stage_ids,
            ["transport", "verification", "classification", "inspection", "inventory_ready", "dispatches"],
        )

    def test_stage_cards_can_be_assigned_individually(self) -> None:
        transport_perm = Permission.objects.get(codename="view_mini_app_egg_stage_transport_card")
        dispatch_perm = Permission.objects.get(codename="view_mini_app_egg_stage_dispatches_card")
        self.user.user_permissions.add(transport_perm, dispatch_perm)
        self._login()

        response = self.client.get(reverse("task_manager:telegram-mini-app"))
        self.assertEqual(response.status_code, 200)
        card_permissions = response.context["mini_app_card_permissions"]
        self.assertTrue(card_permissions["egg_stage_transport"])
        self.assertFalse(card_permissions["egg_stage_verification"])
        self.assertFalse(card_permissions["egg_stage_classification"])
        self.assertFalse(card_permissions["egg_stage_inspection"])
        self.assertFalse(card_permissions["egg_stage_inventory"])
        self.assertTrue(card_permissions["egg_stage_dispatches"])

        egg_workflow = response.context["telegram_mini_app"]["egg_workflow"]
        stage_ids = [stage["id"] for stage in egg_workflow["stages"]]
        self.assertEqual(stage_ids, ["transport", "dispatches"])

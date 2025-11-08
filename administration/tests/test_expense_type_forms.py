from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase

from administration.forms import ExpenseTypeApprovalRuleForm


class ExpenseTypeApprovalRuleFormTests(TestCase):
    def test_limits_approvers_to_staff_or_admin_users(self) -> None:
        user_model = get_user_model()
        staff_user = user_model.objects.create_user(
            email='staff@example.com',
            password='test123',
            is_staff=True,
        )
        admin_user = user_model.objects.create_superuser(
            email='admin@example.com',
            password='test123',
        )
        regular_user = user_model.objects.create_user(
            email='regular@example.com',
            password='test123',
        )

        form = ExpenseTypeApprovalRuleForm()
        approver_queryset = form.fields['approver'].queryset

        self.assertIn(staff_user, approver_queryset)
        self.assertIn(admin_user, approver_queryset)
        self.assertNotIn(regular_user, approver_queryset)


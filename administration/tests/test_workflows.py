from __future__ import annotations

from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from administration.models import (
    ExpenseTypeApprovalRule,
    PurchaseApproval,
    PurchaseRequest,
    PurchasingExpenseType,
    Supplier,
)
from administration.services.workflows import PurchaseApprovalWorkflowService


def _create_expense_type() -> PurchasingExpenseType:
    return PurchasingExpenseType.objects.create(
        name='Gasto test',
    )


def _create_supplier() -> Supplier:
    return Supplier.objects.create(
        name='Tercero Demo',
        tax_id='900123456',
    )


class ExpenseTypeWorkflowViewTests(TestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(
            email='staff@example.com',
            password='test123',
            is_staff=True,
        )
        self.client.force_login(self.user)
        self.expense_type = _create_expense_type()

    def _url(self, params: dict[str, str] | None = None) -> str:
        base = reverse('administration:purchases_configuration')
        if not params:
            return base
        return f"{base}?{urlencode(params)}"

    def test_create_category_with_workflow_steps(self) -> None:
        response = self.client.post(
            self._url({'section': 'expense_types', 'panel': 'expense_type'}),
            {
                'section': 'expense_types',
                'panel': 'expense_type',
                'form_action': 'expense_type',
                'name': 'CapEx granjas',
                'iva_rate': '19.00',
                'withholding_rate': '0.00',
                'parent_category': '',
                'workflow-TOTAL_FORMS': '2',
                'workflow-INITIAL_FORMS': '0',
                'workflow-MIN_NUM_FORMS': '0',
                'workflow-MAX_NUM_FORMS': '1000',
                'workflow-0-approver': str(self.user.pk),
                'workflow-1-approver': '',
            },
        )
        self.assertEqual(response.status_code, 302)
        expense_type = PurchasingExpenseType.objects.get(name='CapEx granjas')
        rules = expense_type.approval_rules.order_by('id')
        self.assertEqual(1, rules.count())
        self.assertEqual(self.user, rules.first().approver)

    def test_update_category_updates_workflow(self) -> None:
        rule = ExpenseTypeApprovalRule.objects.create(
            expense_type=self.expense_type,
            approver=self.user,
        )
        reviewer = get_user_model().objects.create_user(
            email='reviewer@example.com',
            password='test123',
            is_staff=True,
        )
        response = self.client.post(
            self._url({'section': 'expense_types', 'panel': 'expense_type'}),
            {
                'section': 'expense_types',
                'panel': 'expense_type',
                'form_action': 'expense_type',
                'expense_type_id': self.expense_type.pk,
                'name': self.expense_type.name,
                'iva_rate': '19.00',
                'withholding_rate': '0.00',
                'parent_category': '',
                'workflow-TOTAL_FORMS': '2',
                'workflow-INITIAL_FORMS': '1',
                'workflow-MIN_NUM_FORMS': '0',
                'workflow-MAX_NUM_FORMS': '1000',
                'workflow-0-id': str(rule.pk),
                'workflow-0-approver': str(reviewer.pk),
                'workflow-1-approver': str(self.user.pk),
            },
        )
        self.assertEqual(response.status_code, 302)
        rules = list(self.expense_type.approval_rules.order_by('id'))
        self.assertEqual(2, len(rules))
        self.assertEqual(self.user, rules[0].approver)
        self.assertEqual(reviewer, rules[1].approver)

    def test_updating_workflow_rebuilds_existing_purchases(self) -> None:
        requester = get_user_model().objects.create_user(
            email='requester@example.com',
            password='test123',
            is_staff=True,
        )
        supplier = _create_supplier()
        purchase = PurchaseRequest.objects.create(
            timeline_code='SOL-010',
            name='Compra en aprobación',
            requester=requester,
            supplier=supplier,
            expense_type=self.expense_type,
        )
        rule = ExpenseTypeApprovalRule.objects.create(
            expense_type=self.expense_type,
            approver=self.user,
        )
        PurchaseApprovalWorkflowService(
            purchase_request=purchase,
            actor=requester,
        ).run()
        purchase.refresh_from_db()
        self.assertEqual(PurchaseRequest.Status.SUBMITTED, purchase.status)
        initial_approval = purchase.approvals.first()
        assert initial_approval is not None
        self.assertEqual(self.user, initial_approval.approver)

        finance_user = get_user_model().objects.create_user(
            email='finance@example.com',
            password='test123',
            is_staff=True,
        )
        response = self.client.post(
            self._url({'section': 'expense_types', 'panel': 'expense_type'}),
            {
                'section': 'expense_types',
                'panel': 'expense_type',
                'form_action': 'expense_type',
                'expense_type_id': self.expense_type.pk,
                'name': self.expense_type.name,
                'iva_rate': '0.00',
                'withholding_rate': '0.00',
                'parent_category': '',
                'workflow-TOTAL_FORMS': '1',
                'workflow-INITIAL_FORMS': '1',
                'workflow-MIN_NUM_FORMS': '0',
                'workflow-MAX_NUM_FORMS': '1000',
                'workflow-0-id': str(rule.pk),
                'workflow-0-approver': str(finance_user.pk),
            },
        )
        self.assertEqual(response.status_code, 302)
        purchase.refresh_from_db()
        approvals = list(purchase.approvals.order_by('sequence'))
        self.assertEqual(1, len(approvals))
        self.assertEqual(finance_user, approvals[0].approver)
        self.assertEqual(PurchaseRequest.Status.SUBMITTED, purchase.status)


class PurchaseApprovalWorkflowServiceTests(TestCase):
    def setUp(self) -> None:
        self.requester = get_user_model().objects.create_user(
            email='requester@example.com',
            password='test123',
            is_staff=True,
        )
        self.finance = get_user_model().objects.create_user(
            email='finance@example.com',
            password='test123',
            is_staff=True,
        )
        self.expense_type = _create_expense_type()
        self.supplier = _create_supplier()

    def _build_request(self, code: str = 'SOL-001') -> PurchaseRequest:
        return PurchaseRequest.objects.create(
            timeline_code=code,
            name='Compra test',
            description='',
            requester=self.requester,
            supplier=self.supplier,
            expense_type=self.expense_type,
        )

    def test_creates_approvals_and_auto_approves_requester_step(self) -> None:
        ExpenseTypeApprovalRule.objects.create(
            expense_type=self.expense_type,
            approver=self.requester,
        )
        ExpenseTypeApprovalRule.objects.create(
            expense_type=self.expense_type,
            approver=self.finance,
        )
        purchase_request = self._build_request()

        PurchaseApprovalWorkflowService(
            purchase_request=purchase_request,
            actor=self.requester,
        ).run()

        purchase_request.refresh_from_db()
        self.assertEqual(PurchaseRequest.Status.SUBMITTED, purchase_request.status)
        approvals = list(purchase_request.approvals.order_by('sequence'))
        self.assertEqual(2, len(approvals))
        self.assertEqual(PurchaseApproval.Status.APPROVED, approvals[0].status)
        self.assertEqual(PurchaseApproval.Status.PENDING, approvals[1].status)

    def test_all_steps_auto_approved_marks_request_as_approved(self) -> None:
        ExpenseTypeApprovalRule.objects.create(
            expense_type=self.expense_type,
            approver=self.requester,
        )
        ExpenseTypeApprovalRule.objects.create(
            expense_type=self.expense_type,
            approver=self.requester,
        )
        purchase_request = self._build_request(code='SOL-002')

        PurchaseApprovalWorkflowService(
            purchase_request=purchase_request,
            actor=self.requester,
        ).run()

        purchase_request.refresh_from_db()
        self.assertEqual(PurchaseRequest.Status.APPROVED, purchase_request.status)
        self.assertIsNotNone(purchase_request.approved_at)
        approvals = list(purchase_request.approvals.order_by('sequence'))
        self.assertTrue(all(a.status == PurchaseApproval.Status.APPROVED for a in approvals))

    def test_auto_approves_without_rules(self) -> None:
        purchase_request = self._build_request(code='SOL-003')

        PurchaseApprovalWorkflowService(
            purchase_request=purchase_request,
            actor=self.requester,
        ).run()

        purchase_request.refresh_from_db()
        self.assertEqual(PurchaseRequest.Status.APPROVED, purchase_request.status)
        self.assertEqual(1, purchase_request.approvals.count())
        self.assertIsNotNone(purchase_request.approved_at)

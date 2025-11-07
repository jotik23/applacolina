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
                'scope': str(PurchasingExpenseType.Scope.FARM),
                'iva_rate': '19.00',
                'withholding_rate': '0.00',
                'self_withholding_rate': '3.50',
                'parent_category': '',
                'is_active': 'on',
                'workflow-TOTAL_FORMS': '2',
                'workflow-INITIAL_FORMS': '0',
                'workflow-MIN_NUM_FORMS': '0',
                'workflow-MAX_NUM_FORMS': '1000',
                'workflow-0-sequence': '1',
                'workflow-0-name': 'Solicitante',
                'workflow-0-approver': str(self.user.pk),
                'workflow-1-sequence': '',
                'workflow-1-name': '',
                'workflow-1-approver': '',
            },
        )
        self.assertEqual(response.status_code, 302)
        expense_type = PurchasingExpenseType.objects.get(name='CapEx granjas')
        rules = expense_type.approval_rules.order_by('sequence')
        self.assertEqual(1, rules.count())
        self.assertEqual(1, rules.first().sequence)
        self.assertEqual(self.user, rules.first().approver)

    def test_update_category_updates_workflow(self) -> None:
        rule = ExpenseTypeApprovalRule.objects.create(
            expense_type=self.expense_type,
            sequence=1,
            name='Finanzas',
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
                'scope': str(PurchasingExpenseType.Scope.FARM),
                'iva_rate': '19.00',
                'withholding_rate': '0.00',
                'is_active': 'on',
                'self_withholding_rate': '0.00',
                'parent_category': '',
                'workflow-TOTAL_FORMS': '2',
                'workflow-INITIAL_FORMS': '1',
                'workflow-MIN_NUM_FORMS': '0',
                'workflow-MAX_NUM_FORMS': '1000',
                'workflow-0-id': str(rule.pk),
                'workflow-0-sequence': '2',
                'workflow-0-name': 'Finanzas actualizadas',
                'workflow-0-approver': str(reviewer.pk),
                'workflow-1-sequence': '1',
                'workflow-1-name': 'Solicitante',
                'workflow-1-approver': str(self.user.pk),
            },
        )
        self.assertEqual(response.status_code, 302)
        rules = list(self.expense_type.approval_rules.order_by('sequence'))
        self.assertEqual(2, len(rules))
        self.assertEqual('Solicitante', rules[0].name)
        self.assertEqual(self.user, rules[0].approver)
        self.assertEqual('Finanzas actualizadas', rules[1].name)
        self.assertEqual(reviewer, rules[1].approver)


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
            sequence=1,
            name='Solicitante',
            approver=self.requester,
        )
        ExpenseTypeApprovalRule.objects.create(
            expense_type=self.expense_type,
            sequence=2,
            name='Finanzas',
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
            sequence=1,
            name='Solicitante',
            approver=self.requester,
        )
        ExpenseTypeApprovalRule.objects.create(
            expense_type=self.expense_type,
            sequence=2,
            name='Revisión solicitante',
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

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from administration.models import PurchaseRequest, PurchasingExpenseType, Supplier
from administration.services.purchases import get_dashboard_state


class PurchaseDashboardSearchTests(TestCase):
    def setUp(self) -> None:
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            email='dashboard@example.com',
            password='test123',
            is_staff=True,
            cedula='12345',
            nombres='Dashboard',
            apellidos='Tester',
            telefono='3000000001',
        )
        self.manager = user_model.objects.create_user(
            email='gestor@example.com',
            password='test123',
            is_staff=True,
            cedula='67890',
            nombres='Gestor',
            apellidos='Demo',
            telefono='3000000002',
        )
        self.supplier = Supplier.objects.create(name='Proveedor Demo', tax_id='123456789')
        self.category = PurchasingExpenseType.objects.create(name='Infraestructura')

    def test_filters_purchases_by_search_term(self) -> None:
        matching = self._create_purchase(
            timeline_code='CP-100',
            name='Compra motor principal',
            description='Reposición urgente de motor',
            status=PurchaseRequest.Status.DRAFT,
        )
        self._create_purchase(
            timeline_code='CP-101',
            name='Compra luminarias',
            description='Iluminación exterior',
            status=PurchaseRequest.Status.DRAFT,
        )

        state = self._dashboard_state(
            scope=PurchaseRequest.Status.DRAFT,
            search='motor',
        )

        self.assertEqual([matching.pk], [purchase.pk for purchase in state.pagination.records])

    def test_search_does_not_cross_scope_boundaries(self) -> None:
        draft = self._create_purchase(
            timeline_code='CP-200',
            name='Generador backup',
            description='Generador diesel 40k',
            status=PurchaseRequest.Status.DRAFT,
        )
        self._create_purchase(
            timeline_code='CP-201',
            name='Generador backup',
            description='Generador diesel 40k',
            status=PurchaseRequest.Status.SUBMITTED,
        )

        state = self._dashboard_state(
            scope=PurchaseRequest.Status.DRAFT,
            search='Generador',
        )

        self.assertEqual([draft.pk], [purchase.pk for purchase in state.pagination.records])

    def test_filters_by_start_date(self) -> None:
        old_purchase = self._create_purchase(
            timeline_code='CP-300',
            name='Compra antigua',
            description='Hace semanas',
            status=PurchaseRequest.Status.DRAFT,
            created_at=timezone.now() - timedelta(days=15),
        )
        recent = self._create_purchase(
            timeline_code='CP-301',
            name='Compra reciente',
            description='Semana actual',
            status=PurchaseRequest.Status.DRAFT,
            created_at=timezone.now() - timedelta(days=2),
        )

        start_date = (timezone.now() - timedelta(days=7)).date()
        state = self._dashboard_state(
            scope=PurchaseRequest.Status.DRAFT,
            start_date=start_date,
        )

        self.assertEqual([recent.pk], [purchase.pk for purchase in state.pagination.records])
        self.assertNotIn(old_purchase.pk, [purchase.pk for purchase in state.pagination.records])

    def test_filters_by_end_date(self) -> None:
        early = self._create_purchase(
            timeline_code='CP-400',
            name='Compra temprana',
            description='Mes pasado',
            status=PurchaseRequest.Status.DRAFT,
            created_at=timezone.now() - timedelta(days=20),
        )
        late = self._create_purchase(
            timeline_code='CP-401',
            name='Compra tardía',
            description='Esta semana',
            status=PurchaseRequest.Status.DRAFT,
            created_at=timezone.now() - timedelta(days=1),
        )

        end_date = (timezone.now() - timedelta(days=10)).date()
        state = self._dashboard_state(
            scope=PurchaseRequest.Status.DRAFT,
            end_date=end_date,
        )

        self.assertEqual([early.pk], [purchase.pk for purchase in state.pagination.records])
        self.assertNotIn(late.pk, [purchase.pk for purchase in state.pagination.records])

    def test_paginates_results_in_batches_of_thirty(self) -> None:
        for idx in range(35):
            self._create_purchase(
                timeline_code=f'CP-5{idx:02d}',
                name=f'Compra #{idx}',
                description='Paginar',
                status=PurchaseRequest.Status.DRAFT,
            )

        first_page = self._dashboard_state(scope=PurchaseRequest.Status.DRAFT, page=1)
        second_page = self._dashboard_state(scope=PurchaseRequest.Status.DRAFT, page=2)

        self.assertEqual(30, len(first_page.pagination.records))
        self.assertTrue(first_page.pagination.has_next)
        self.assertEqual(35, first_page.pagination.count)
        self.assertEqual(2, first_page.pagination.num_pages)

        self.assertEqual(5, len(second_page.pagination.records))
        self.assertTrue(second_page.pagination.has_previous)
        self.assertFalse(second_page.pagination.has_next)
        self.assertEqual(2, second_page.pagination.page_number)

    def test_invalid_page_number_defaults_to_last_page(self) -> None:
        for idx in range(40):
            self._create_purchase(
                timeline_code=f'CP-6{idx:02d}',
                name=f'Compra #{idx}',
                description='Paginar',
                status=PurchaseRequest.Status.DRAFT,
            )

        state = self._dashboard_state(scope=PurchaseRequest.Status.DRAFT, page=99)

        self.assertEqual(2, state.pagination.page_number)
        self.assertFalse(state.pagination.has_next)
        self.assertTrue(state.pagination.has_previous)

    def test_filters_by_category_selection(self) -> None:
        other_category = PurchasingExpenseType.objects.create(name='Bioseguridad')
        match = self._create_purchase(
            timeline_code='CP-700',
            name='Compra filtro',
            description='Filtro especial',
            status=PurchaseRequest.Status.DRAFT,
            expense_type=other_category,
        )
        self._create_purchase(
            timeline_code='CP-701',
            name='Compra general',
            description='Otro gasto',
            status=PurchaseRequest.Status.DRAFT,
        )

        state = self._dashboard_state(
            scope=PurchaseRequest.Status.DRAFT,
            categories=[other_category.pk],
        )

        self.assertEqual([match.pk], [purchase.pk for purchase in state.pagination.records])

    def test_filters_by_supplier_selection(self) -> None:
        other_supplier = Supplier.objects.create(name='Proveedor Beta', tax_id='555')
        match = self._create_purchase(
            timeline_code='CP-710',
            name='Compra proveedor beta',
            description='Proveedor especial',
            status=PurchaseRequest.Status.DRAFT,
            supplier=other_supplier,
        )
        self._create_purchase(
            timeline_code='CP-711',
            name='Compra proveedor alfa',
            description='Proveedor base',
            status=PurchaseRequest.Status.DRAFT,
        )

        state = self._dashboard_state(
            scope=PurchaseRequest.Status.DRAFT,
            suppliers=[other_supplier.pk],
        )

        self.assertEqual([match.pk], [purchase.pk for purchase in state.pagination.records])

    def test_filters_by_manager_selection(self) -> None:
        match = self._create_purchase(
            timeline_code='CP-720',
            name='Compra con gestor',
            description='Gestor asignado',
            status=PurchaseRequest.Status.DRAFT,
            assigned_manager=self.manager,
        )
        self._create_purchase(
            timeline_code='CP-721',
            name='Compra sin gestor',
            description='Sin asignar',
            status=PurchaseRequest.Status.DRAFT,
        )

        state = self._dashboard_state(
            scope=PurchaseRequest.Status.DRAFT,
            managers=[self.manager.pk],
        )

        self.assertEqual([match.pk], [purchase.pk for purchase in state.pagination.records])

    def test_grouped_purchases_show_aggregated_amounts(self) -> None:
        dominant_category = PurchasingExpenseType.objects.create(name='Bioseguridad')
        leader = PurchaseRequest.objects.create(
            timeline_code='CP-800',
            name='Compra líder',
            description='Agrupada',
            requester=self.user,
            supplier=self.supplier,
            expense_type=self.category,
            status=PurchaseRequest.Status.INVOICE,
            currency='COP',
            estimated_total=Decimal('1000000'),
            invoice_total=Decimal('800000'),
            payment_amount=Decimal('200000'),
            support_group_code='SG-020',
        )
        follower = PurchaseRequest.objects.create(
            timeline_code='CP-801',
            name='Compra seguidora',
            description='Agrupada 2',
            requester=self.user,
            supplier=self.supplier,
            expense_type=dominant_category,
            status=PurchaseRequest.Status.INVOICE,
            currency='COP',
            estimated_total=Decimal('900000'),
            invoice_total=Decimal('1200000'),
            payment_amount=Decimal('150000'),
            support_group_code='SG-020',
            support_group_leader=leader,
        )
        state = self._dashboard_state(scope=PurchaseRequest.Status.INVOICE)
        records = {record.pk: record for record in state.pagination.records}
        self.assertIn(leader.pk, records)
        self.assertIn(follower.pk, records)
        expected_total = Decimal('2000000')
        expected_paid = Decimal('350000')
        self.assertEqual(expected_total, records[leader.pk].total_amount)
        self.assertEqual(expected_paid, records[leader.pk].paid_amount)
        self.assertEqual(dominant_category.name, records[leader.pk].category_name)
        self.assertEqual(expected_total, records[follower.pk].total_amount)
        self.assertEqual(expected_paid, records[follower.pk].paid_amount)
        self.assertEqual(dominant_category.name, records[follower.pk].category_name)

    def _create_purchase(
        self,
        *,
        timeline_code: str,
        name: str,
        description: str,
        status: str,
        created_at: datetime | None = None,
        expense_type: PurchasingExpenseType | None = None,
        supplier: Supplier | None = None,
        assigned_manager=None,
    ) -> PurchaseRequest:
        purchase = PurchaseRequest.objects.create(
            timeline_code=timeline_code,
            name=name,
            description=description,
            requester=self.user,
            supplier=supplier or self.supplier,
            expense_type=expense_type or self.category,
            assigned_manager=assigned_manager,
            status=status,
            currency='COP',
            estimated_total=Decimal('1000000.00'),
        )
        if created_at:
            PurchaseRequest.objects.filter(pk=purchase.pk).update(created_at=created_at)
            purchase.refresh_from_db(fields=['created_at'])
        return purchase

    def _dashboard_state(
        self,
        *,
        scope: str,
        search: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        page: int | None = None,
        categories: list[int] | None = None,
        suppliers: list[int] | None = None,
        managers: list[int] | None = None,
    ):
        return get_dashboard_state(
            scope_code=scope,
            panel_code=None,
            purchase_pk=None,
            search_query=search or None,
            start_date=start_date,
            end_date=end_date,
            page_number=page,
            category_ids=categories,
            supplier_ids=suppliers,
            manager_ids=managers,
        )

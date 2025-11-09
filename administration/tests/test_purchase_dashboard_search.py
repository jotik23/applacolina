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

    def _create_purchase(
        self,
        *,
        timeline_code: str,
        name: str,
        description: str,
        status: str,
        created_at: datetime | None = None,
    ) -> PurchaseRequest:
        purchase = PurchaseRequest.objects.create(
            timeline_code=timeline_code,
            name=name,
            description=description,
            requester=self.user,
            supplier=self.supplier,
            expense_type=self.category,
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
    ):
        return get_dashboard_state(
            scope_code=scope,
            panel_code=None,
            purchase_pk=None,
            search_query=search or None,
            start_date=start_date,
            end_date=end_date,
            page_number=page,
        )

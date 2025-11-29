from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.test import TestCase

from administration.models import Sale, SaleItem, SalePayment, SaleProductType, Supplier
from administration.services.sales import build_sales_cardex
from personal.models import UserProfile
from production.models import EggDispatch, EggDispatchDestination, EggDispatchItem, EggType


class SalesCardexServiceTestCase(TestCase):
    def setUp(self) -> None:
        self.customer = Supplier.objects.create(name="Cliente Demo", tax_id="900321654")
        self.seller = UserProfile.objects.create_user(
            cedula="123456789",
            password="secret",
            nombres="Vendedor",
            apellidos="Principal",
            telefono="3000000000",
            is_staff=True,
        )
        self.driver = UserProfile.objects.create_user(
            cedula="999888777",
            password="secret",
            nombres="Conductor",
            apellidos="Principal",
            telefono="3110000000",
            is_staff=True,
        )
        self.destination = EggDispatchDestination.TIERRALTA

    def _dispatch(self, dispatch_date: date, cartons: Decimal) -> EggDispatch:
        dispatch = EggDispatch.objects.create(
            date=dispatch_date,
            destination=self.destination,
            driver=self.driver,
            seller=self.seller,
            total_cartons=cartons,
        )
        EggDispatchItem.objects.create(dispatch=dispatch, egg_type=EggType.JUMBO, cartons=cartons)
        return dispatch

    def _sale(self, sale_date: date, quantity: Decimal, unit_price: Decimal, invoice: str) -> Sale:
        sale = Sale.objects.create(
            date=sale_date,
            customer=self.customer,
            seller=self.seller,
            warehouse_destination=self.destination,
            status=Sale.Status.CONFIRMED,
            payment_condition=Sale.PaymentCondition.CREDIT,
            invoice_number=invoice,
        )
        SaleItem.objects.create(
            sale=sale,
            product_type=SaleProductType.JUMBO,
            quantity=quantity,
            unit_price=unit_price,
        )
        return sale

    def test_assigns_sales_fifo_and_payments(self) -> None:
        dispatch_one = self._dispatch(dispatch_date=date(2024, 1, 2), cartons=Decimal("100"))
        dispatch_two = self._dispatch(dispatch_date=date(2024, 1, 5), cartons=Decimal("80"))
        sale_one = self._sale(sale_date=date(2024, 1, 3), quantity=Decimal("70"), unit_price=Decimal("1000"), invoice="F001")
        SalePayment.objects.create(sale=sale_one, date=date(2024, 1, 4), amount=Decimal("30000"))
        SalePayment.objects.create(sale=sale_one, date=date(2024, 1, 6), amount=Decimal("40000"))
        sale_two = self._sale(sale_date=date(2024, 1, 7), quantity=Decimal("90"), unit_price=Decimal("1200"), invoice="F002")
        SalePayment.objects.create(sale=sale_two, date=date(2024, 1, 8), amount=Decimal("60000"))
        SalePayment.objects.create(sale=sale_two, date=date(2024, 1, 10), amount=Decimal("48000"))

        result = build_sales_cardex(seller_ids=[self.seller.pk], destinations=[self.destination])
        self.assertEqual(len(result.rows), 2)
        # Rows are returned from newest to oldest
        latest_row = result.rows[0]
        oldest_row = result.rows[1]

        self.assertEqual(latest_row.dispatch.pk, dispatch_two.pk)
        self.assertEqual(oldest_row.dispatch.pk, dispatch_one.pk)

        jumbo_key = SaleProductType.JUMBO

        self.assertEqual(latest_row.sold_by_type.get(jumbo_key), Decimal("60"))
        self.assertEqual(latest_row.closing_balance.get(jumbo_key), Decimal("20"))
        self.assertEqual(oldest_row.sold_by_type.get(jumbo_key), Decimal("100"))
        self.assertEqual(oldest_row.closing_balance.get(jumbo_key), Decimal("0"))

        self.assertEqual(oldest_row.total_amount, Decimal("106000"))
        self.assertEqual(oldest_row.payments_total, Decimal("106000"))
        self.assertEqual(oldest_row.balance_due, Decimal("0"))
        self.assertEqual(oldest_row.collection_duration_days, 6)
        self.assertEqual(latest_row.total_amount, Decimal("72000"))
        self.assertEqual(latest_row.payments_total, Decimal("72000"))
        self.assertEqual(latest_row.balance_due, Decimal("0"))
        self.assertEqual(latest_row.collection_duration_days, 2)

        latest_sales = latest_row.sorted_sales()
        self.assertEqual(len(latest_sales), 1)
        self.assertEqual(latest_sales[0].sale.pk, sale_two.pk)
        oldest_sales = oldest_row.sorted_sales()
        self.assertEqual(len(oldest_sales), 2)
        self.assertEqual({summary.sale.pk for summary in oldest_sales}, {sale_one.pk, sale_two.pk})

    def test_marks_sales_without_inventory(self) -> None:
        sale = self._sale(sale_date=date(2024, 2, 1), quantity=Decimal("50"), unit_price=Decimal("900"), invoice="F010")
        SalePayment.objects.create(sale=sale, date=date(2024, 2, 2), amount=Decimal("10000"))

        result = build_sales_cardex(seller_ids=[self.seller.pk], destinations=[self.destination])
        self.assertEqual(result.rows, [])
        self.assertEqual(len(result.unassigned_items), 1)
        unassigned = result.unassigned_items[0]
        self.assertEqual(unassigned.sale.pk, sale.pk)
        self.assertEqual(unassigned.product_type, SaleProductType.JUMBO)
        self.assertEqual(unassigned.quantity, Decimal("50"))
        self.assertEqual(unassigned.reason, "missing_inventory")

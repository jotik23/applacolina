from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from administration.forms import SaleForm, SalePaymentForm
from administration.models import Sale, SaleItem, SalePayment, SaleProductType, Supplier
from personal.models import UserProfile
from production.models import EggDispatch, EggDispatchDestination, EggDispatchItem, EggType


class SaleFormTestCase(TestCase):
    def setUp(self) -> None:
        self.customer = Supplier.objects.create(name="Cliente Demo", tax_id="900123456")
        self.seller = UserProfile.objects.create_user(
            cedula="10203040",
            password="secret",
            nombres="Vendedor",
            apellidos="Demo",
            telefono="3000000000",
            is_staff=True,
        )
        self.driver = UserProfile.objects.create_user(
            cedula="55667788",
            password="secret",
            nombres="Conductor",
            apellidos="Demo",
            telefono="3110000000",
            is_staff=True,
        )
        self.default_destination = EggDispatchDestination.TIERRALTA
        dispatch = EggDispatch.objects.create(
            date=date.today(),
            destination=self.default_destination,
            driver=self.driver,
            seller=self.seller,
            total_cartons=Decimal("500"),
        )
        EggDispatchItem.objects.create(dispatch=dispatch, egg_type=EggType.JUMBO, cartons=Decimal("500"))

    def _base_form_data(self) -> dict[str, str]:
        tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()
        return {
            "date": timezone.localdate().isoformat(),
            "customer": str(self.customer.pk),
            "seller": str(self.seller.pk),
            "status": Sale.Status.CONFIRMED,
            "warehouse_destination": self.default_destination,
            "payment_condition": Sale.PaymentCondition.CREDIT,
            "payment_due_date": tomorrow,
            "discount_amount": "0",
            "notes": "Entrega programada",
            "quantity_jumbo": "12",
            "unit_price_jumbo": "18000",
        }

    def test_sale_requires_destination_and_inventory(self):
        data = self._base_form_data()
        data["warehouse_destination"] = ""
        form = SaleForm(data=data, actor_id=self.seller.pk)
        self.assertFalse(form.is_valid())
        self.assertIn("Selecciona la bodega", form.errors["warehouse_destination"][0])

    def test_confirmed_sale_requires_available_inventory(self):
        dispatch = EggDispatch.objects.create(
            date=date.today(),
            destination=EggDispatchDestination.TIERRALTA,
            driver=self.driver,
            seller=self.seller,
            total_cartons=Decimal("20"),
        )
        EggDispatchItem.objects.create(dispatch=dispatch, egg_type=EggType.JUMBO, cartons=Decimal("20"))
        data = self._base_form_data()
        data.update({"warehouse_destination": EggDispatchDestination.TIERRALTA, "quantity_jumbo": "10"})
        form = SaleForm(data=data, actor_id=self.seller.pk)
        self.assertTrue(form.is_valid(), form.errors)
        sale = form.save()
        self.assertEqual(sale.status, Sale.Status.CONFIRMED)
        self.assertEqual(sale.warehouse_destination, EggDispatchDestination.TIERRALTA)
        self.assertIsNotNone(sale.confirmed_at)

    def test_confirmed_sale_allows_quantity_exceeding_inventory(self):
        dispatch = EggDispatch.objects.create(
            date=date.today(),
            destination=EggDispatchDestination.MONTERIA,
            driver=self.driver,
            seller=self.seller,
            total_cartons=Decimal("5"),
        )
        EggDispatchItem.objects.create(dispatch=dispatch, egg_type=EggType.JUMBO, cartons=Decimal("5"))
        data = self._base_form_data()
        data.update(
            {
                "status": Sale.Status.CONFIRMED,
                "warehouse_destination": EggDispatchDestination.MONTERIA,
                "quantity_jumbo": "12",
            }
        )
        form = SaleForm(data=data, actor_id=self.seller.pk)
        self.assertTrue(form.is_valid(), form.errors)
        sale = form.save()
        item = sale.items.get(product_type=SaleProductType.JUMBO)
        self.assertEqual(item.quantity, Decimal("12"))

    def test_discount_and_withholding_are_calculated(self):
        data = self._base_form_data()
        data.update(
            {
                "quantity_jumbo": "100",
                "unit_price_jumbo": "1000",
                "discount_amount": "5000",
            }
        )
        form = SaleForm(data=data, actor_id=self.seller.pk)
        self.assertTrue(form.is_valid(), form.errors)
        sale = form.save()
        self.assertEqual(sale.discount_amount, Decimal("5000"))
        self.assertEqual(sale.total_amount, Decimal("95000"))
        self.assertEqual(sale.auto_withholding_amount, Decimal("950"))

    def test_payment_form_prevents_amount_greater_than_balance(self):
        sale = Sale.objects.create(
            date=timezone.localdate(),
            customer=self.customer,
            seller=self.seller,
            status=Sale.Status.CONFIRMED,
            payment_condition=Sale.PaymentCondition.CREDIT,
            payment_due_date=timezone.localdate(),
        )
        SaleItem.objects.create(
            sale=sale,
            product_type=SaleProductType.JUMBO,
            quantity=Decimal("100"),
            unit_price=Decimal("1000"),
        )
        form = SalePaymentForm(
            data={
                "date": timezone.localdate().isoformat(),
                "amount": "150000",
                "method": "cash",
                "notes": "Pago excedido",
            },
            sale=sale,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("supera el saldo", form.errors["amount"][0])

    def test_balance_due_does_not_subtract_withholding(self):
        sale = Sale.objects.create(
            date=timezone.localdate(),
            customer=self.customer,
            seller=self.seller,
            status=Sale.Status.CONFIRMED,
            payment_condition=Sale.PaymentCondition.CREDIT,
            payment_due_date=timezone.localdate(),
        )
        SaleItem.objects.create(
            sale=sale,
            product_type=SaleProductType.JUMBO,
            quantity=Decimal("100"),
            unit_price=Decimal("1000"),
        )
        self.assertEqual(sale.auto_withholding_amount, Decimal("1000"))
        self.assertEqual(sale.balance_due, Decimal("100000"))
        SalePayment.objects.create(
            sale=sale,
            amount=Decimal("20000"),
            method=SalePayment.Method.TRANSFER,
        )
        self.assertEqual(sale.balance_due, Decimal("80000"))

    def test_retention_reduces_total(self):
        data = self._base_form_data()
        data.update(
            {
                "quantity_jumbo": "50",
                "unit_price_jumbo": "2000",
                "retention_amount": "10",
            }
        )
        form = SaleForm(data=data, actor_id=self.seller.pk)
        self.assertTrue(form.is_valid(), form.errors)
        sale = form.save()
        self.assertEqual(sale.retention_amount, Decimal("10"))
        self.assertEqual(sale.retention_value, Decimal("10000"))
        self.assertEqual(sale.total_amount, Decimal("90000"))

    def test_retention_cannot_exceed_total(self):
        data = self._base_form_data()
        data.update(
            {
                "quantity_jumbo": "10",
                "unit_price_jumbo": "1000",
                "retention_amount": "150",
            }
        )
        form = SaleForm(data=data, actor_id=self.seller.pk)
        self.assertFalse(form.is_valid())
        self.assertIn("no puede superar", form.errors["retention_amount"][0])

    def test_edit_form_prefills_dates(self):
        sale = Sale.objects.create(
            date=timezone.localdate() - timedelta(days=5),
            payment_due_date=timezone.localdate() + timedelta(days=2),
            customer=self.customer,
            seller=self.seller,
            status=Sale.Status.CONFIRMED,
            payment_condition=Sale.PaymentCondition.CASH,
        )
        form = SaleForm(instance=sale, actor_id=self.seller.pk)
        self.assertEqual(form.fields["date"].initial, sale.date)
        self.assertEqual(form.fields["payment_due_date"].initial, sale.payment_due_date)
        self.assertEqual(str(form["date"].value()), sale.date.isoformat())
        self.assertEqual(str(form["payment_due_date"].value()), sale.payment_due_date.isoformat())

    def test_payment_form_allows_editing_existing_payment(self):
        sale = Sale.objects.create(
            date=timezone.localdate(),
            customer=self.customer,
            seller=self.seller,
            status=Sale.Status.CONFIRMED,
            payment_condition=Sale.PaymentCondition.CREDIT,
            payment_due_date=timezone.localdate(),
        )
        SaleItem.objects.create(
            sale=sale,
            product_type=SaleProductType.JUMBO,
            quantity=Decimal("100"),
            unit_price=Decimal("1000"),
        )
        payment = SalePayment.objects.create(
            sale=sale,
            date=timezone.localdate(),
            amount=Decimal("50000"),
            method=SalePayment.Method.CASH,
        )
        form = SalePaymentForm(
            data={
                "date": timezone.localdate().isoformat(),
                "amount": "60000",
                "method": SalePayment.Method.CASH,
                "notes": "Ajuste",
            },
            sale=sale,
            instance=payment,
        )
        self.assertTrue(form.is_valid(), form.errors)
        updated = form.save()
        self.assertEqual(updated.amount, Decimal("60000"))

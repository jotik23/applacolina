from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from administration.forms import SaleForm, SalePaymentForm
from administration.models import Sale, SaleItem, SaleProductType, Supplier
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

    def _base_form_data(self) -> dict[str, str]:
        tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()
        return {
            "date": timezone.localdate().isoformat(),
            "customer": str(self.customer.pk),
            "seller": str(self.seller.pk),
            "status": Sale.Status.DRAFT,
            "warehouse_destination": "",
            "payment_condition": Sale.PaymentCondition.CREDIT,
            "payment_due_date": tomorrow,
            "discount_amount": "0",
            "notes": "Entrega programada",
            "quantity_jumbo": "12",
            "unit_price_jumbo": "18000",
        }

    def test_prefactura_can_be_saved_without_inventory_validation(self):
        form = SaleForm(data=self._base_form_data(), actor_id=self.seller.pk)
        self.assertTrue(form.is_valid(), form.errors)
        sale = form.save()
        self.assertEqual(sale.status, Sale.Status.DRAFT)
        self.assertEqual(sale.items.count(), 1)
        item = sale.items.first()
        assert item is not None
        self.assertEqual(item.quantity, Decimal("12"))
        self.assertEqual(item.unit_price, Decimal("18000"))

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
        data.update(
            {
                "status": Sale.Status.CONFIRMED,
                "warehouse_destination": EggDispatchDestination.TIERRALTA,
                "quantity_jumbo": "10",
            }
        )
        form = SaleForm(data=data, actor_id=self.seller.pk)
        self.assertTrue(form.is_valid(), form.errors)
        sale = form.save()
        self.assertEqual(sale.status, Sale.Status.CONFIRMED)
        self.assertEqual(sale.warehouse_destination, EggDispatchDestination.TIERRALTA)
        self.assertIsNotNone(sale.confirmed_at)

    def test_confirmed_sale_blocks_if_requested_quantity_exceeds_inventory(self):
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
        self.assertFalse(form.is_valid())
        error_list = form.errors.get(form.quantity_field_map[SaleProductType.JUMBO], [])
        self.assertTrue(any("Inventario insuficiente" in error for error in error_list))

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

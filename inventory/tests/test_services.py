from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.test import TestCase

from administration.models import Product
from inventory.models import InventoryScope, ProductInventoryBalance
from inventory.services import InventoryService
from production.models import ChickenHouse, Farm, Room


class InventoryServiceTests(TestCase):
    def setUp(self) -> None:
        self.product = Product.objects.create(name="Concentrado", unit=Product.Unit.UNIT, category=Product.Category.FOOD)
        self.farm = Farm.objects.create(name="Granja Uno")
        self.chicken_house = ChickenHouse.objects.create(name="Galpón 1", farm=self.farm)
        self.room = Room.objects.create(name="Sala", chicken_house=self.chicken_house, area_m2=Decimal("10"))
        self.service = InventoryService(actor=None)

    def test_register_receipt_updates_balance(self) -> None:
        entry = self.service.register_receipt(
            product=self.product,
            scope=InventoryScope.CHICKEN_HOUSE,
            quantity=Decimal("25"),
            farm=self.farm,
            chicken_house=self.chicken_house,
        )
        self.assertIsNotNone(entry)
        balance = ProductInventoryBalance.objects.get(product=self.product, scope=InventoryScope.CHICKEN_HOUSE, chicken_house=self.chicken_house)
        self.assertEqual(balance.quantity, Decimal("25"))

    def test_manual_consumption_creates_out_entry(self) -> None:
        self.service.register_receipt(
            product=self.product,
            scope=InventoryScope.CHICKEN_HOUSE,
            quantity=Decimal("10"),
            farm=self.farm,
            chicken_house=self.chicken_house,
        )
        self.service.register_manual_consumption(
            product=self.product,
            scope=InventoryScope.CHICKEN_HOUSE,
            quantity=Decimal("4"),
            farm=self.farm,
            chicken_house=self.chicken_house,
            notes="Prueba",
            executed_by=None,
        )
        balance = ProductInventoryBalance.objects.get(product=self.product, scope=InventoryScope.CHICKEN_HOUSE, chicken_house=self.chicken_house)
        self.assertEqual(balance.quantity, Decimal("6"))

    def test_reset_scope_overrides_balance(self) -> None:
        self.service.register_receipt(
            product=self.product,
            scope=InventoryScope.COMPANY,
            quantity=Decimal("40"),
            farm=None,
            chicken_house=None,
        )
        self.service.reset_scope(
            product=self.product,
            scope=InventoryScope.COMPANY,
            new_quantity=Decimal("12"),
            notes="Conteo",
        )
        balance = ProductInventoryBalance.objects.get(product=self.product, scope=InventoryScope.COMPANY)
        self.assertEqual(balance.quantity, Decimal("12"))

    def test_auto_consumption_prioritizes_specific_scope(self) -> None:
        self.service.register_receipt(
            product=self.product,
            scope=InventoryScope.CHICKEN_HOUSE,
            quantity=Decimal("5"),
            farm=self.farm,
            chicken_house=self.chicken_house,
        )
        self.service.register_receipt(
            product=self.product,
            scope=InventoryScope.FARM,
            quantity=Decimal("10"),
            farm=self.farm,
            chicken_house=None,
        )
        self.service.register_receipt(
            product=self.product,
            scope=InventoryScope.COMPANY,
            quantity=Decimal("20"),
            farm=None,
            chicken_house=None,
        )
        self.service.consume_for_room_record(
            room=self.room,
            product=self.product,
            quantity=Decimal("12"),
            effective_date=date.today(),
            notes="Auto",
            reference=None,
            recorded_by=None,
        )
        balance_ch = ProductInventoryBalance.objects.get(product=self.product, scope=InventoryScope.CHICKEN_HOUSE, chicken_house=self.chicken_house)
        balance_farm = ProductInventoryBalance.objects.get(product=self.product, scope=InventoryScope.FARM, farm=self.farm)
        balance_company = ProductInventoryBalance.objects.get(product=self.product, scope=InventoryScope.COMPANY)
        self.assertEqual(balance_ch.quantity, Decimal("0"))
        self.assertEqual(balance_farm.quantity, Decimal("3"))
        self.assertEqual(balance_company.quantity, Decimal("20"))

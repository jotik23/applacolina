from django.test import TestCase

from production.models import ChickenHouse, Farm


class ChickenHouseModelTests(TestCase):
    def test_destination_defaults_to_farm(self) -> None:
        farm_origin = Farm.objects.create(name="Granja Norte")
        farm_destination = Farm.objects.create(name="Granja Central")
        house = ChickenHouse.objects.create(farm=farm_origin, name="Galpón Alfa")
        self.assertEqual(house.egg_destination_farm, farm_origin)

        house.egg_destination_farm = farm_destination
        house.save()
        house.refresh_from_db()
        self.assertEqual(house.egg_destination_farm, farm_destination)

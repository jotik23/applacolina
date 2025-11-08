from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("administration", "0024_product_purchaseitem_product"),
    ]

    operations = [
        migrations.AlterField(
            model_name="product",
            name="unit",
            field=models.CharField(
                choices=[
                    ("Bultos", "Bultos"),
                    ("Paquete x 100", "Paquete x 100"),
                    ("Paquete x 120", "Paquete x 120"),
                    ("Unidad", "Unidad"),
                ],
                default="Unidad",
                max_length=60,
                verbose_name="Unidad",
            ),
        ),
    ]

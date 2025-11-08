from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("administration", "0023_purchaserequest_scope_area"),
    ]

    operations = [
        migrations.CreateModel(
            name="Product",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=150, unique=True, verbose_name="Nombre")),
                ("unit", models.CharField(default="Unidad", max_length=60, verbose_name="Unidad")),
            ],
            options={
                "verbose_name": "Producto",
                "verbose_name_plural": "Productos",
                "ordering": ("name",),
            },
        ),
        migrations.AddField(
            model_name="purchaseitem",
            name="product",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.SET_NULL,
                related_name="purchase_items",
                to="administration.product",
                verbose_name="Producto",
            ),
        ),
    ]

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("administration", "0038_remove_sale_auto_withholding_amount_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="sale",
            name="invoice_number",
            field=models.CharField(
                blank=True,
                db_index=True,
                default="",
                max_length=64,
                verbose_name="Número de factura",
            ),
        ),
        migrations.AddField(
            model_name="sale",
            name="sent_to_dian",
            field=models.BooleanField(default=False, verbose_name="Enviado a la DIAN"),
        ),
    ]

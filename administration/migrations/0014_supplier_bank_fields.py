from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("administration", "0013_remove_support_concept_template"),
    ]

    operations = [
        migrations.AddField(
            model_name="supplier",
            name="account_holder_id",
            field=models.CharField(blank=True, max_length=50, verbose_name="Identificación titular"),
        ),
        migrations.AddField(
            model_name="supplier",
            name="account_holder_name",
            field=models.CharField(blank=True, max_length=255, verbose_name="Nombre titular"),
        ),
        migrations.AddField(
            model_name="supplier",
            name="account_number",
            field=models.CharField(blank=True, max_length=60, verbose_name="Número de cuenta"),
        ),
        migrations.AddField(
            model_name="supplier",
            name="account_type",
            field=models.CharField(blank=True, choices=[("ahorros", "Ahorros"), ("corriente", "Corriente")], max_length=20, verbose_name="Tipo de cuenta"),
        ),
        migrations.AddField(
            model_name="supplier",
            name="bank_name",
            field=models.CharField(blank=True, max_length=120, verbose_name="Banco"),
        ),
    ]

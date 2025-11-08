from django.db import migrations, models


def populate_scope_area(apps, schema_editor):
    PurchaseRequest = apps.get_model('administration', 'PurchaseRequest')
    PurchaseRequest.objects.filter(scope_chicken_house__isnull=False).update(scope_area='chicken_house')
    PurchaseRequest.objects.filter(scope_chicken_house__isnull=True, scope_farm__isnull=False).update(scope_area='farm')


class Migration(migrations.Migration):

    dependencies = [
        ('administration', '0022_remove_purchasingexpensetype_is_active'),
    ]

    operations = [
        migrations.AddField(
            model_name='purchaserequest',
            name='scope_area',
            field=models.CharField(
                choices=[
                    ('company', 'Empresa'),
                    ('farm', 'Granja'),
                    ('chicken_house', 'Galpón'),
                ],
                default='company',
                max_length=20,
                verbose_name='Área',
            ),
        ),
        migrations.RunPython(populate_scope_area, migrations.RunPython.noop),
    ]


from django.db import migrations, models


def assign_scope(apps, schema_editor):
    Config = apps.get_model('inventory', 'ProductConsumptionConfig')
    for config in Config.objects.select_related('room__chicken_house__farm'):
        room = getattr(config, 'room', None)
        if not room:
            continue
        chicken_house = room.chicken_house
        farm = chicken_house.farm if chicken_house else None
        config.scope = 'chicken_house'
        config.chicken_house_id = chicken_house.pk if chicken_house else None
        config.farm_id = farm.pk if farm else None
        config.save(update_fields=['scope', 'chicken_house', 'farm'])


class Migration(migrations.Migration):

    dependencies = [
        ('production', '0023_eggclassificationbatch_transport_confirmed_at_and_more'),
        ('inventory', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='productconsumptionconfig',
            name='scope',
            field=models.CharField(choices=[('farm', 'Granja'), ('chicken_house', 'Galpón')], default='chicken_house', max_length=20),
        ),
        migrations.AddField(
            model_name='productconsumptionconfig',
            name='farm',
            field=models.ForeignKey(blank=True, null=True, on_delete=models.CASCADE, related_name='product_consumption_configs', to='production.farm'),
        ),
        migrations.AddField(
            model_name='productconsumptionconfig',
            name='chicken_house',
            field=models.ForeignKey(blank=True, null=True, on_delete=models.CASCADE, related_name='product_consumption_configs', to='production.chickenhouse'),
        ),
        migrations.RunPython(assign_scope, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='productconsumptionconfig',
            name='room',
        ),
        migrations.RemoveField(
            model_name='productconsumptionconfig',
            name='end_date',
        ),
        migrations.AlterModelOptions(
            name='productconsumptionconfig',
            options={
                'ordering': ('-start_date', 'scope', 'chicken_house__name', 'farm__name'),
                'verbose_name': 'Configuración de consumo por salón',
                'verbose_name_plural': 'Configuraciones de consumo por salón',
            },
        ),
    ]

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('administration', '0012_supportdocumenttype_and_more'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='purchasingexpensetype',
            name='support_concept_template',
        ),
    ]

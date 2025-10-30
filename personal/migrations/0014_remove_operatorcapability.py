from django.db import migrations


class Migration(migrations.Migration):

    replaces = [('calendario', '0014_remove_operatorcapability')]

    dependencies = [
        ("personal", "0013_remove_positiondefinition_allow_lower_complexity_and_more"),
    ]

    operations = [
        migrations.DeleteModel(
            name="OperatorCapability",
        ),
    ]

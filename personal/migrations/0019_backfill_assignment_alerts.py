from django.db import migrations


def mark_unauthorized_assignments(apps, schema_editor):
    ShiftAssignment = apps.get_model("personal", "ShiftAssignment")

    assignments = ShiftAssignment.objects.select_related("operator", "position").prefetch_related(
        "operator__suggested_positions"
    )

    for assignment in assignments:
        operator = assignment.operator
        position = assignment.position
        if not operator or not position:
            continue

        if operator.suggested_positions.filter(pk=position.pk).exists():
            continue

        desired_level = "critical" if assignment.is_auto_assigned else "warn"

        if assignment.alert_level == desired_level:
            continue

        assignment.alert_level = desired_level
        assignment.save(update_fields=["alert_level", "updated_at"])


class Migration(migrations.Migration):

    replaces = [('calendario', '0019_backfill_assignment_alerts')]

    dependencies = [
        ("personal", "0018_shiftassignment_uniq_calendar_operator_date"),
    ]

    operations = [
        migrations.RunPython(mark_unauthorized_assignments, migrations.RunPython.noop),
    ]

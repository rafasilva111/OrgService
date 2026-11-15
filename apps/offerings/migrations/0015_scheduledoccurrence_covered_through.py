from django.db import migrations, models
from django.db.models import F


def fill_covered_through(apps, schema_editor):
    occurrence = apps.get_model("offerings", "ScheduledOccurrence")
    occurrence.objects.using(schema_editor.connection.alias).update(
        covered_through=F("scheduled_date")
    )


class Migration(migrations.Migration):
    dependencies = [
        ("offerings", "0014_alter_notification_task_scheduledoccurrence"),
    ]

    operations = [
        migrations.AddField(
            model_name="scheduledoccurrence",
            name="covered_through",
            field=models.DateField(null=True),
        ),
        migrations.RunPython(fill_covered_through, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="scheduledoccurrence",
            name="covered_through",
            field=models.DateField(),
        ),
    ]

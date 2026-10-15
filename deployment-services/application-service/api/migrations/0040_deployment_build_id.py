from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0039_backfill_legacy_task_family"),
    ]

    operations = [
        migrations.AddField(
            model_name="deployment",
            name="build_id",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
    ]

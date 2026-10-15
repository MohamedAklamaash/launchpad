from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0040_deployment_build_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="application",
            name="build_task_id",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name="deployment",
            name="build_task_id",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
    ]

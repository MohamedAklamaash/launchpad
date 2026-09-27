from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0028_platform_dns"),
    ]

    operations = [
        migrations.AddField(
            model_name="infrastructure",
            name="exited_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]

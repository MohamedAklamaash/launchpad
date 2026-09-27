import shared.utils.uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0029_infrastructure_exited_at"),
    ]

    operations = [
        migrations.CreateModel(
            name="ExitExportAccess",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=shared.utils.uuid.uuid7_pk,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("request_id", models.CharField(max_length=36)),
                ("user_id", models.UUIDField()),
                ("infrastructure_id", models.UUIDField(blank=True, null=True)),
                ("action", models.CharField(default="export", max_length=20)),
                ("status_code", models.IntegerField()),
                ("app_count", models.IntegerField(default=0)),
                ("bytes", models.IntegerField(default=0)),
                ("duration_ms", models.IntegerField(default=0)),
                ("client_ip", models.CharField(blank=True, max_length=64, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "db_table": "exit_export_access",
            },
        ),
        migrations.AddIndex(
            model_name="exitexportaccess",
            index=models.Index(fields=["infrastructure_id", "created_at"], name="exit_export_infra_idx"),
        ),
        migrations.AddIndex(
            model_name="exitexportaccess",
            index=models.Index(fields=["user_id", "created_at"], name="exit_export_user_idx"),
        ),
    ]

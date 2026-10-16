import shared.utils.uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0041_build_task_id'),
    ]

    operations = [
        migrations.CreateModel(
            name='BuildLogAccess',
            fields=[
                ('id', models.UUIDField(default=shared.utils.uuid.uuid7_pk, editable=False, primary_key=True, serialize=False)),
                ('request_id', models.CharField(max_length=36)),
                ('user_id', models.UUIDField()),
                ('app_id', models.UUIDField(blank=True, null=True)),
                ('deployment_id', models.UUIDField(blank=True, null=True)),
                ('infra_id', models.UUIDField(blank=True, null=True)),
                ('cursor_present', models.BooleanField(default=False)),
                ('status_code', models.IntegerField()),
                ('aws_error_code', models.CharField(blank=True, max_length=64, null=True)),
                ('event_count', models.IntegerField(default=0)),
                ('bytes', models.IntegerField(default=0)),
                ('duration_ms', models.IntegerField(default=0)),
                ('client_ip', models.CharField(blank=True, max_length=64, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'db_table': 'build_log_access',
                'indexes': [
                    models.Index(fields=['app_id', 'created_at'], name='build_log_app_id_idx'),
                    models.Index(fields=['user_id', 'created_at'], name='build_log_user_id_idx'),
                ],
            },
        ),
    ]

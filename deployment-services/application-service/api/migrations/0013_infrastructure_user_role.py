from django.db import migrations, models
import django.db.models.deletion
from shared.utils.uuid import uuid7_pk


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0012_add_sleep_fields'),
    ]

    operations = [
        migrations.CreateModel(
            name='InfrastructureUserRole',
            fields=[
                ('id', models.UUIDField(default=uuid7_pk, editable=False, primary_key=True, serialize=False)),
                ('role', models.CharField(
                    choices=[
                        ('admin', 'Admin'),
                        ('user', 'User'),
                        ('guest', 'Guest'),
                        ('super_admin', 'Super Admin')
                    ],
                    default='user',
                    max_length=20
                )),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('infrastructure', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='user_roles',
                    to='api.infrastructure'
                )),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='infrastructure_roles',
                    to='api.user'
                )),
            ],
        ),
        migrations.AddIndex(
            model_name='infrastructureuserrole',
            index=models.Index(fields=['infrastructure', 'user'], name='api_infrast_infrast_idx'),
        ),
        migrations.AddIndex(
            model_name='infrastructureuserrole',
            index=models.Index(fields=['user', 'role'], name='api_infrast_user_id_idx'),
        ),
        migrations.AlterUniqueTogether(
            name='infrastructureuserrole',
            unique_together={('infrastructure', 'user')},
        ),
    ]

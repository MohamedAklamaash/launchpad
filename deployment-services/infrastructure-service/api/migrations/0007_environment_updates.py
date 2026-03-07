from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0006_environment_logs'),
    ]

    operations = [
        migrations.AlterField(
            model_name='environment',
            name='status',
            field=models.CharField(
                choices=[
                    ('PENDING', 'Pending'),
                    ('PROVISIONING', 'Provisioning'),
                    ('ACTIVE', 'Active'),
                    ('ERROR', 'Error'),
                    ('DESTROYING', 'Destroying'),
                    ('DESTROYED', 'Destroyed')
                ],
                default='PENDING',
                max_length=50
            ),
        ),
        migrations.AddField(
            model_name='environment',
            name='error_message',
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='environment',
            name='retry_count',
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name='environment',
            name='locked_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='environment',
            name='locked_by',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddIndex(
            model_name='environment',
            index=models.Index(fields=['status', 'locked_at'], name='environment_status_locked_idx'),
        ),
    ]

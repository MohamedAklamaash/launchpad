from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0013_infrastructure_user_role'),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name='application',
            unique_together={('user', 'name')},
        ),
        migrations.AlterUniqueTogether(
            name='infrastructure',
            unique_together={('user', 'name')},
        ),
        migrations.AddIndex(
            model_name='application',
            index=models.Index(fields=['user', 'infrastructure'], name='api_applica_user_id_idx'),
        ),
        migrations.AddIndex(
            model_name='application',
            index=models.Index(fields=['status'], name='api_applica_status_idx'),
        ),
        migrations.AddIndex(
            model_name='infrastructure',
            index=models.Index(fields=['user', 'is_cloud_authenticated'], name='api_infrast_user_id_auth_idx'),
        ),
    ]

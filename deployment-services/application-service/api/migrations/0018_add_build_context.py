from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0017_fix_app_unique_per_infra'),
    ]

    operations = [
        migrations.AddField(
            model_name='application',
            name='build_context',
            field=models.CharField(blank=True, default='', max_length=255, null=True),
        ),
    ]

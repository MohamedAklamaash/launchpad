from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('api', '0019_add_alb_security_group_id'),
    ]
    operations = [
        migrations.AddField(
            model_name='application',
            name='github_webhook_secret',
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
    ]

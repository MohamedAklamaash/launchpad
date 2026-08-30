from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('api', '0011_add_alb_security_group_id'),
    ]
    operations = [
        migrations.AddField(
            model_name='infrastructure',
            name='onboarding_token_hash',
            field=models.CharField(blank=True, max_length=128, null=True),
        ),
        migrations.AddField(
            model_name='infrastructure',
            name='onboarding_token_used_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]

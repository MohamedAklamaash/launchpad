from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [
        ('api', '0010_application'),
    ]
    operations = [
        migrations.AddField(
            model_name='environment',
            name='alb_security_group_id',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
    ]

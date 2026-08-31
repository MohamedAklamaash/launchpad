from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0024_purge_credential_metadata'),
    ]

    operations = [
        migrations.AddField(
            model_name='application',
            name='runtime_refs',
            field=models.JSONField(blank=True, null=True),
        ),
    ]

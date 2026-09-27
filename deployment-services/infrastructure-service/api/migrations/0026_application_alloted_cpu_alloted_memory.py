from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0025_purge_credential_metadata'),
    ]

    operations = [
        migrations.AddField(
            model_name='application',
            name='alloted_cpu',
            field=models.FloatField(default=0.0),
        ),
        migrations.AddField(
            model_name='application',
            name='alloted_memory',
            field=models.FloatField(default=0.0),
        ),
    ]

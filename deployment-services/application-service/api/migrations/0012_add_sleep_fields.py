from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0011_add_port_field'),
    ]

    operations = [
        migrations.AddField(
            model_name='application',
            name='is_sleeping',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='application',
            name='desired_count',
            field=models.IntegerField(default=1),
        ),
        migrations.AlterField(
            model_name='application',
            name='status',
            field=models.CharField(
                choices=[
                    ('CREATED', 'Created'),
                    ('BUILDING', 'Building'),
                    ('PUSHING_IMAGE', 'Pushing Image'),
                    ('DEPLOYING', 'Deploying'),
                    ('ACTIVE', 'Active'),
                    ('SLEEPING', 'Sleeping'),
                    ('FAILED', 'Failed')
                ],
                default='CREATED',
                max_length=50
            ),
        ),
    ]

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0015_rename_api_applica_user_id_idx_api_applica_user_id_6d458f_idx_and_more'),
    ]

    operations = [
        migrations.DeleteModel(
            name='InfrastructureUserRole',
        ),
    ]

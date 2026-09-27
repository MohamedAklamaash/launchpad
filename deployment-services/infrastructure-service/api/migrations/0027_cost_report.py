import django.db.models.deletion
import shared.utils.uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0026_application_alloted_cpu_alloted_memory'),
    ]

    operations = [
        migrations.CreateModel(
            name='CostReport',
            fields=[
                ('id', models.UUIDField(default=shared.utils.uuid.uuid7_pk, editable=False, primary_key=True, serialize=False)),
                ('window_start', models.DateField()),
                ('window_end', models.DateField()),
                ('computed_on', models.DateField()),
                ('payload', models.JSONField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('infrastructure', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='cost_reports', to='api.infrastructure')),
            ],
        ),
        migrations.AddConstraint(
            model_name='costreport',
            constraint=models.UniqueConstraint(fields=('infrastructure', 'window_start', 'window_end'), name='unique_cost_report_window'),
        ),
    ]

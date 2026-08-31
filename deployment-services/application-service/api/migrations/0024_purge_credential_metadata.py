from django.db import migrations

CREDENTIAL_KEYS = ("aws_access_key_id", "aws_secret_access_key", "aws_session_token")


def purge_credential_metadata(apps, schema_editor):
    Infrastructure = apps.get_model("api", "Infrastructure")
    for infra in Infrastructure.objects.exclude(metadata__isnull=True).iterator():
        metadata = infra.metadata or {}
        if not any(key in metadata for key in CREDENTIAL_KEYS):
            continue
        for key in CREDENTIAL_KEYS:
            metadata.pop(key, None)
        infra.metadata = metadata
        infra.save(update_fields=["metadata"])


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0023_infrastructure_compute_type_environment_status'),
    ]

    operations = [
        migrations.RunPython(purge_credential_metadata, migrations.RunPython.noop),
    ]

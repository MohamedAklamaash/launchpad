"""Backfill Environment.status values dropped by 0024.

0024 replaced the choices (PROVISIONING/READY/FAILED/DESTROYING) with the
infrastructure-service set (PENDING/PROVISIONING/ACTIVE/ERROR/DESTROYING/DESTROYED)
but altered only the field, leaving any legacy row on a value that is no longer a
valid choice. `_validate_infrastructure` accepts ACTIVE only, so a surviving READY
row would report its environment as not deployable.
"""
from django.db import migrations

_FORWARD = {"READY": "ACTIVE", "FAILED": "ERROR"}
_BACKWARD = {"ACTIVE": "READY", "ERROR": "FAILED"}


def _remap(apps, mapping):
    Environment = apps.get_model("api", "Environment")
    for old, new in mapping.items():
        Environment.objects.filter(status=old).update(status=new)


def forwards(apps, schema_editor):
    _remap(apps, _FORWARD)


def backwards(apps, schema_editor):
    # Lossy by nature: an environment that was always ACTIVE is indistinguishable from
    # one backfilled from READY. Reversible so the migration can be rolled back at all.
    _remap(apps, _BACKWARD)


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0027_app_name_unique_per_infra'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]

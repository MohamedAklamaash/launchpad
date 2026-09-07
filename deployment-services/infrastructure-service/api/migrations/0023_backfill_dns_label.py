import secrets

from django.db import migrations

# Mirrors Infrastructure.mint_dns_label. Deliberately duplicated rather than imported:
# a migration must keep working against the historical model state even if the method is
# later changed or removed.
MAX_ATTEMPTS = 5


def backfill(apps, schema_editor):
    """Give every pre-existing infrastructure a dns_label.

    New rows get one at create. Without this, every infrastructure onboarded before this
    migration keeps dns_label=NULL, and the gap only surfaces when HTTPS activation tries
    to build a hostname for them — at which point it is a production incident rather than
    a migration.
    """
    Infrastructure = apps.get_model('api', 'Infrastructure')
    ReservedDnsLabel = apps.get_model('api', 'ReservedDnsLabel')

    taken = set(ReservedDnsLabel.objects.values_list('label', flat=True))
    for infra in Infrastructure.objects.filter(dns_label__isnull=True).iterator():
        for _ in range(MAX_ATTEMPTS):
            candidate = secrets.token_hex(8)
            if candidate in taken:
                continue
            taken.add(candidate)
            ReservedDnsLabel.objects.create(label=candidate, infrastructure_id=infra.id)
            infra.dns_label = candidate
            infra.save(update_fields=['dns_label'])
            break
        else:
            raise RuntimeError(
                f"Could not mint a unique dns_label for infrastructure {infra.id} "
                f"after {MAX_ATTEMPTS} attempts — check the RNG."
            )


def unbackfill(apps, schema_editor):
    """Deliberately a no-op.

    Labels are never reused, so dropping the reservations on reverse would let a later mint
    hand a retired label to a different tenant — exactly the collision this whole column
    exists to prevent. Reversing the schema change is enough; the tombstones stay.
    """


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0022_custom_domain'),
    ]

    operations = [
        migrations.RunPython(backfill, unbackfill),
    ]

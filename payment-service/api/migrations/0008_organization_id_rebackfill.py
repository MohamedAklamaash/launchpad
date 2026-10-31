from django.db import migrations
from shared.utils.org_id import personal_org_id


def backfill_organization_id(apps, schema_editor):
    """Stamps rows created after 0006 ran but before create paths wrote the column. With
    ORGS_ENABLED on, a NULL organization_id is inaccessible, so this runs once more before
    any flag flip. Same personal-org formula as 0006; rows already stamped are untouched,
    which makes a re-run a no-op."""
    Infrastructure = apps.get_model('api', 'Infrastructure')
    Billing = apps.get_model('api', 'Billing')

    owner_ids = Infrastructure.objects.filter(
        organization_id__isnull=True, user_id__isnull=False,
    ).values_list('user_id', flat=True).distinct()
    for owner_id in owner_ids:
        Infrastructure.objects.filter(
            user_id=owner_id, organization_id__isnull=True,
        ).update(organization_id=personal_org_id(owner_id))

    owner_ids = Billing.objects.filter(
        organization_id__isnull=True,
    ).values_list('user_id', flat=True).distinct()
    for owner_id in owner_ids:
        Billing.objects.filter(
            user_id=owner_id, organization_id__isnull=True,
        ).update(organization_id=personal_org_id(owner_id))


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0007_org_events_read_model"),
    ]

    operations = [
        migrations.RunPython(backfill_organization_id, migrations.RunPython.noop),
    ]

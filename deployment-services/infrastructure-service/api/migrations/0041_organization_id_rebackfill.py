from django.db import migrations
from shared.utils.org_id import personal_org_id


def backfill_organization_id(apps, schema_editor):
    """Stamps rows created after 0039 ran but before create paths wrote the column. With
    ORGS_ENABLED on, a NULL organization_id is inaccessible, so this runs once more before
    any flag flip. Infrastructure rows get the personal-org formula 0039 used. A Database takes
    its Infrastructure's organization_id, so the two always agree. Rows already stamped are
    untouched, which makes a re-run a no-op."""
    Infrastructure = apps.get_model('api', 'Infrastructure')
    Database = apps.get_model('api', 'Database')

    owner_ids = Infrastructure.objects.filter(
        organization_id__isnull=True,
    ).values_list('user_id', flat=True).distinct()
    for owner_id in owner_ids:
        Infrastructure.objects.filter(
            user_id=owner_id, organization_id__isnull=True,
        ).update(organization_id=personal_org_id(owner_id))

    org_ids = Database.objects.filter(
        organization_id__isnull=True, environment__infrastructure__organization_id__isnull=False,
    ).values_list('environment__infrastructure__organization_id', flat=True).distinct()
    for org_id in org_ids:
        Database.objects.filter(
            environment__infrastructure__organization_id=org_id, organization_id__isnull=True,
        ).update(organization_id=org_id)


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0040_org_events_read_model"),
    ]

    operations = [
        migrations.RunPython(backfill_organization_id, migrations.RunPython.noop),
    ]

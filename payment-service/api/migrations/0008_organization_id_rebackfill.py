from django.db import migrations
from shared.utils.org_id import personal_org_id


def backfill_organization_id(apps, schema_editor):
    """Stamps rows created after 0006 ran but before create paths wrote the column. With
    ORGS_ENABLED on, a NULL organization_id is inaccessible, so this runs once more before
    any flag flip. Infrastructure rows get the personal-org formula 0006 used. A Billing row
    takes the organization_id of the infrastructure named in its metadata, falling back to
    the payer's personal org. Rows already stamped are untouched, which makes a re-run a
    no-op."""
    Infrastructure = apps.get_model('api', 'Infrastructure')
    Billing = apps.get_model('api', 'Billing')

    owner_ids = Infrastructure.objects.filter(
        organization_id__isnull=True, user_id__isnull=False,
    ).values_list('user_id', flat=True).distinct()
    for owner_id in owner_ids:
        Infrastructure.objects.filter(
            user_id=owner_id, organization_id__isnull=True,
        ).update(organization_id=personal_org_id(owner_id))

    for billing in Billing.objects.filter(organization_id__isnull=True):
        infra_id = (billing.metadata or {}).get('infrastructure_id')
        infra = Infrastructure.objects.filter(id=infra_id).first() if infra_id else None
        if infra is not None and infra.organization_id is not None:
            org_id = infra.organization_id
        else:
            org_id = personal_org_id(billing.user_id)
        Billing.objects.filter(pk=billing.pk, organization_id__isnull=True).update(organization_id=org_id)


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0007_org_events_read_model"),
    ]

    operations = [
        migrations.RunPython(backfill_organization_id, migrations.RunPython.noop),
    ]

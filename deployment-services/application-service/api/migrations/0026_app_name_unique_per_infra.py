from django.db import migrations

from api.common.naming import app_slug


def find_duplicate_slugs(rows):
    """rows: iterable of (app_id, infrastructure_id, name).
    Returns {(infrastructure_id, slug): [(app_id, name), ...]} for slugs claimed more than once."""
    by_slug = {}
    for app_id, infrastructure_id, name in rows:
        by_slug.setdefault((str(infrastructure_id), app_slug(name)), []).append((str(app_id), name))
    return {key: apps for key, apps in by_slug.items() if len(apps) > 1}


def refuse_duplicate_slugs(apps, schema_editor):
    Application = apps.get_model('api', 'Application')
    rows = Application.objects.values_list('id', 'infrastructure_id', 'name')
    duplicates = find_duplicate_slugs(rows)
    if not duplicates:
        return
    offenders = "\n".join(
        f"  infrastructure={infrastructure_id} slug={slug}: "
        + ", ".join(f"{app_id} ({name})" for app_id, name in collisions)
        for (infrastructure_id, slug), collisions in sorted(duplicates.items())
    )
    raise RuntimeError(
        "Cannot scope application names to infrastructure: these applications derive the same "
        f"deployment slug and would overwrite each other.\n{offenders}\n"
        "Rename or delete the duplicates, then re-run the migration."
    )


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0025_application_runtime_refs'),
    ]

    operations = [
        migrations.RunPython(refuse_duplicate_slugs, migrations.RunPython.noop),
        migrations.AlterUniqueTogether(
            name='application',
            unique_together={('infrastructure', 'name')},
        ),
    ]

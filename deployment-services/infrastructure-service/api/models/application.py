from django.db import models


class Application(models.Model):
    id = models.CharField(max_length=255, primary_key=True)
    infrastructure_id = models.CharField(max_length=255, db_index=True)
    name = models.CharField(max_length=255)
    user_id = models.CharField(max_length=255)
    # Read-model fields synced from application.created/application.updated (F4): the EKS
    # cost estimate needs the pod resource request each app was deployed with, which this
    # service otherwise has no way to see — application-service owns the Application row.
    # No `status` field: deploy-time status transitions (BUILDING/ACTIVE/FAILED/...) are
    # never published as events (see application_deployment_service.py), so a replicated
    # status would silently go stale. The estimate instead treats every row present here
    # (deletions ARE published and remove the row) as running for the full query window —
    # deliberately coarse, and always labelled source="estimate".
    alloted_cpu = models.FloatField(default=0.0)
    alloted_memory = models.FloatField(default=0.0)

    class Meta:
        db_table = 'applications'

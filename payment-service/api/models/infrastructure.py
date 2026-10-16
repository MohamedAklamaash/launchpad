from django.db import models


class Infrastructure(models.Model):
    id = models.UUIDField(primary_key=True, editable=False)
    user_id = models.UUIDField(null=True, blank=True) # Changed to UUIDField with null=True for resilience
    name = models.CharField(max_length=255, null=True, blank=True)
    status = models.CharField(max_length=50, default='active')
    # Personal-org backfill, nullable until a later migration confirms the backfill
    # is complete and flips this to NOT NULL.
    # Computed as uuid5(ORG_NAMESPACE, user_id) -- see shared/utils/org_id.py. Left
    # null for a row whose user_id is itself null (nothing to compute from). Nothing
    # reads this yet.
    organization_id = models.UUIDField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name or self.id} (User: {self.user_id})"

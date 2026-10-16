from django.db import models


class Organization(models.Model):
    """Thin read-model mirror of auth-service's canonical Organization row, kept in sync
    via the org.events consumer (F9 ticket 4, plan/F9-teams-and-orgs.md). `id` is never
    locally generated — it is the organization_id carried on every org.events payload
    (for a personal org, uuid5(ORG_NAMESPACE, legacy_user_id); see shared/utils/org_id.py).

    `version` is the per-Membership monotonic counter (host_readiness_version's pattern)
    carried on the org.created payload that created this row — stored so a redelivered or
    out-of-order org.created event can be rejected the same way every other org.events
    payload is, never so this service invents its own org-level counter.
    """

    id = models.UUIDField(primary_key=True, editable=False)
    owner_account_id = models.UUIDField()
    version = models.IntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

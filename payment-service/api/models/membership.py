from django.db import models
from shared.enums.org_role import OrgRole

from api.common.utils.uuid import uuid7_pk


class Membership(models.Model):
    """Thin read-model mirror of auth-service's canonical Membership row (F9 ticket 4).
    `account_id` has no local Account mirror in this service — org.events carries only the
    raw id.

    `version` is the per-row monotonic counter minted by auth-service under a row lock on
    every Membership write (host_readiness_version's pattern) — a consumer never applies a
    payload whose version isn't strictly greater than what's stored here.
    """

    id = models.UUIDField(primary_key=True, default=uuid7_pk, editable=False)
    organization = models.ForeignKey(
        "api.Organization",
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    account_id = models.UUIDField()
    role = models.CharField(max_length=20, choices=OrgRole.choices, default=OrgRole.VIEWER)
    version = models.IntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("organization", "account_id")]

from django.db import models
from shared.enums.org_role import OrgRole

from api.common.utils.uuid import uuid7_pk


class MembershipQuerySet(models.QuerySet):
    def active(self):
        """Excludes a tombstoned (removed_at IS NOT NULL) row. Ticket 7's permission
        resolution must go through this, never the bare manager, or a removed member's
        last-known role would still count them as a current member."""
        return self.filter(removed_at__isnull=True)


class Membership(models.Model):
    """Thin read-model mirror of auth-service's canonical Membership row (F9 ticket 4).
    `account_id` has no local Account mirror in this service — org.events carries only the
    raw id.

    `version` is the per-row monotonic counter minted by auth-service under a row lock on
    every Membership write (host_readiness_version's pattern) — a consumer never applies a
    payload whose version isn't strictly greater than what's stored here.

    `removed_at` makes org.membership.removed a tombstone, never a hard delete — mirroring
    auth-service's own removeMember (membership.service.ts). A hard delete would leave no
    row for a stale, redelivered org.membership.changed to be rejected against, letting it
    recreate the row and hand a removed member their role back (the resurrection race this
    ticket closes). A re-add is a later org.membership.changed/org.invitation.accepted with
    a higher version, applied to this same row (clearing removed_at), never a second row.
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
    removed_at = models.DateTimeField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = MembershipQuerySet.as_manager()

    class Meta:
        unique_together = [("organization", "account_id")]

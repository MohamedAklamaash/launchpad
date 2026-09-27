from django.db import models
from shared.utils.uuid import uuid7_pk


class PlatformDnsRecord(models.Model):
    """Ledger of every record the platform DNS writer currently believes is live in the
    platform Route53 zone for a given infrastructure.

    `infrastructure_id` is a plain UUID field, not a ForeignKey — the same reasoning as
    ReservedDnsLabel: `delete_infrastructure` hard-deletes the Infrastructure row for
    PENDING/DESTROYED infras, and a live ledger row must outlive that delete so teardown
    can still find and remove the real Route53 record afterward. A ForeignKey would either
    cascade this row away (silently orphaning the DNS record) or require PROTECT/SET_NULL,
    both wrong for a row whose entire purpose is to be read *after* its parent is gone.

    A row is inserted before the matching Route53 UPSERT is issued and deleted only after
    a confirmed Route53 DELETE — never the reverse — so "this table is empty for infra X"
    is always a safe (if occasionally stale-toward-existing) proxy for "nothing of ours is
    left in the zone for infra X". `delete_infrastructure` refuses a hard delete while any
    row for that infrastructure is still live (see api/services/infrastructure.py).

    Stores the full resolved record, not just infrastructure_id + kind, so teardown after a
    hard delete never needs to reconstruct a name from a dns_label that may no longer be
    readable from the Infrastructure table itself.
    """

    KIND_EDGE = "edge"
    KIND_WILDCARD = "wildcard"
    KIND_VALIDATION = "validation"
    KIND_CHOICES = [
        (KIND_EDGE, "Edge indirection"),
        (KIND_WILDCARD, "Wildcard to edge"),
        (KIND_VALIDATION, "ACM validation"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid7_pk, editable=False)
    infrastructure_id = models.UUIDField(db_index=True)
    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    # Canonical form: no trailing dot, '*' not '\\052'. Normalized once at the Route53
    # client boundary (api/services/platform_dns/naming.py) so the ledger, the fake zone,
    # and every diff compare like-for-like.
    record_name = models.CharField(max_length=255)
    record_type = models.CharField(max_length=10, default="CNAME")
    record_value = models.CharField(max_length=512)
    ttl = models.IntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["infrastructure_id", "record_name", "record_type"],
                name="unique_platform_dns_record_per_infra",
            ),
        ]
        indexes = [
            models.Index(fields=["infrastructure_id"]),
        ]

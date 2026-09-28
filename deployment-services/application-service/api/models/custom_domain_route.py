from django.db import models
from shared.utils.uuid import uuid7_pk


class CustomDomainRoute(models.Model):
    """application-service's own record of a custom domain's ALB wiring (F1b part 3b).

    `CustomDomain` (the claim/validate state machine, the ACM certificate) lives in
    infrastructure-service's database — a separate service, separate DB. This is not a
    read-model mirror of it; it exists only so this service's own teardown paths (app
    delete, in particular) can find and remove the exact rule/certificate ARNs *they*
    created without a round trip back to infrastructure-service to ask what they were.
    `infrastructure_id`/`application_id` are cross-service references, not Django FKs.

    One row per attached hostname — created by the internal attach endpoint
    (api/views/custom_domains_internal.py), deleted by detach. `hostname` is unique: the
    same cross-tenant exclusivity infrastructure-service's partial unique index enforces
    on `CustomDomain` holds here too, for the same reason (one hostname routes to exactly
    one place).
    """

    id = models.UUIDField(primary_key=True, default=uuid7_pk, editable=False)
    infrastructure_id = models.UUIDField()
    application_id = models.UUIDField()
    hostname = models.CharField(max_length=255, unique=True)
    cert_arn = models.CharField(max_length=512)
    host_forward_rule_arn = models.CharField(max_length=512)
    host_redirect_rule_arn = models.CharField(max_length=512, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'custom_domain_routes'
        indexes = [
            models.Index(fields=['application_id']),
            models.Index(fields=['infrastructure_id']),
        ]

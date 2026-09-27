from django.db import models
from shared.utils.uuid import uuid7_pk


class Environment(models.Model):
    id = models.UUIDField(
        primary_key=True,
        default=uuid7_pk,
        editable=False,
    )
    infrastructure = models.ForeignKey(
        'Infrastructure',
        on_delete=models.CASCADE,
        related_name='environments',
    )
    
    vpc_id = models.CharField(max_length=255, null=True, blank=True)
    cluster_arn = models.CharField(max_length=512, null=True, blank=True)
    alb_arn = models.CharField(max_length=512, null=True, blank=True)
    alb_dns = models.CharField(max_length=512, null=True, blank=True)
    alb_security_group_id = models.CharField(max_length=255, null=True, blank=True)
    target_group_arn = models.CharField(max_length=512, null=True, blank=True)
    ecr_repository_url = models.CharField(max_length=512, null=True, blank=True)
    ecs_task_execution_role_arn = models.CharField(max_length=512, null=True, blank=True)
    # Set once terraform applies the conditional 443 listener (modules/alb, enable_https).
    # Null on every environment that has never had a certificate ISSUED. Read by
    # application-service to decide whether a per-app host-header 443 rule can be created,
    # and used as one leg of the "host URL is safe to publish" gate alongside
    # InfrastructureCertificate.tls_status == ISSUED and the DNS ledger's synced_at.
    https_listener_arn = models.CharField(max_length=512, null=True, blank=True)
    # EKS counterpart to https_listener_arn: set once the cluster's shared IngressClassParams
    # object has been patched with this infra's ACM certificate ARN and the 80+443 listen
    # ports (see api/services/eks_bootstrap.py:apply_eks_tls). ECS never sets this — its
    # readiness signal is https_listener_arn instead. Null for every EKS infra whose
    # certificate isn't ISSUED yet, or whose patch attempt hasn't succeeded.
    eks_ingress_tls_ready = models.BooleanField(default=False)
    # R3 (security review): the ACM certificate ARN eks_ingress_tls_ready was last patched
    # (or is currently being attempted) against. A cert re-issue after a FAILED/re-request
    # cycle gets a new ARN — comparing against this is how the TLS sweep
    # (run_worker.py:_apply_eks_tls_for_issued_certs) notices the patched cert is stale and
    # re-patches, instead of eks_ingress_tls_ready staying True forever once set. Cleared on
    # teardown alongside eks_ingress_tls_ready.
    eks_ingress_tls_cert_arn = models.CharField(max_length=255, null=True, blank=True)
    # When the current attempt cycle against eks_ingress_tls_cert_arn started — reset
    # whenever that target cert_arn changes, and used to bound how long a persistently
    # failing patch attempt (unreachable cluster, k8s API throttling) gets retried every
    # ~30s tick before giving up, mirroring cert_bootstrap.ISSUED_CHECK_TIMEOUT's own
    # age-based cutoff for the PENDING-certificate loop. Null once patched successfully.
    eks_ingress_tls_patch_attempted_at = models.DateTimeField(null=True, blank=True)

    status = models.CharField(
        max_length=50,
        choices=[
            ('PENDING', 'Pending'),
            ('PROVISIONING', 'Provisioning'),
            ('ACTIVE', 'Active'),
            ('UPDATING', 'Updating'),
            ('ERROR', 'Error'),
            ('DESTROYING', 'Destroying'),
            ('DESTROYED', 'Destroyed'),
        ],
        default='PENDING'
    )

    first_activated_at = models.DateTimeField(null=True, blank=True)

    logs = models.TextField(null=True, blank=True)
    error_message = models.TextField(null=True, blank=True)
    retry_count = models.IntegerField(default=0)
    locked_at = models.DateTimeField(null=True, blank=True)
    locked_by = models.CharField(max_length=255, null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'environments'
        indexes = [
            models.Index(fields=['status', 'locked_at']),
        ]

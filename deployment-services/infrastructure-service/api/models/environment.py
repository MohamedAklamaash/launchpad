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

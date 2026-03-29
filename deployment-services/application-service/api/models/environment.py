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
    
    # Terraform outputs
    vpc_id = models.CharField(max_length=255, null=True, blank=True)
    cluster_arn = models.CharField(max_length=512, null=True, blank=True)
    alb_arn = models.CharField(max_length=512, null=True, blank=True)
    alb_dns = models.CharField(max_length=512, null=True, blank=True)
    alb_security_group_id = models.CharField(max_length=255, null=True, blank=True)
    target_group_arn = models.CharField(max_length=512, null=True, blank=True)
    ecr_repository_url = models.CharField(max_length=512, null=True, blank=True)
    ecs_task_execution_role_arn = models.CharField(max_length=512, null=True, blank=True)
    
    status = models.CharField(
        max_length=50,
        choices=[
            ('PROVISIONING', 'Provisioning'),
            ('READY', 'Ready'),
            ('FAILED', 'Failed'),
            ('DESTROYING', 'Destroying'),
        ],
        default='PROVISIONING'
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'environments'

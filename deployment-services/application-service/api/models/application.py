from shared.utils.uuid import uuid7_pk
from django.db import models
from django.conf import settings
from api.models.infrastructure import Infrastructure

class Application(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid7_pk, editable=False)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    infrastructure = models.ForeignKey(Infrastructure, on_delete=models.CASCADE)
    
    alloted_cpu = models.FloatField(default=0.0)
    alloted_memory = models.FloatField(default=0.0)
    alloted_storage = models.FloatField(default=0.0)

    project_remote_url = models.CharField(max_length=255)
    project_branch = models.CharField(max_length=255)
    project_commit_hash = models.CharField(max_length=255)
    
    class Meta:
        unique_together = [('user', 'infrastructure', 'name')]  # App name unique per infra
        indexes = [
            models.Index(fields=['user', 'infrastructure']),
            models.Index(fields=['status']),
        ]
    version = models.IntegerField(default=1)

    dockerfile_path = models.CharField(max_length=255, default="Dockerfile", blank=True)
    port = models.IntegerField(default=8080)  # Container port
    build_command = models.CharField(max_length=255, blank=True, null=True)
    start_command = models.CharField(max_length=255, blank=True, null=True)
    install_command = models.CharField(max_length=255, blank=True, null=True)

    envs = models.JSONField(default=dict, null=True, blank=True)
    metadata = models.JSONField(default=dict, null=True, blank=True)
    
    status = models.CharField(
        max_length=50,
        choices=[
            ('CREATED', 'Created'),
            ('BUILDING', 'Building'),
            ('PUSHING_IMAGE', 'Pushing Image'),
            ('DEPLOYING', 'Deploying'),
            ('ACTIVE', 'Active'),
            ('SLEEPING', 'Sleeping'),
            ('FAILED', 'Failed'),
        ],
        default='CREATED'
    )
    
    # Deployment resources
    task_definition_arn = models.CharField(max_length=512, null=True, blank=True)
    service_arn = models.CharField(max_length=512, null=True, blank=True)
    target_group_arn = models.CharField(max_length=512, null=True, blank=True)
    listener_rule_arn = models.CharField(max_length=512, null=True, blank=True)
    deployment_url = models.CharField(max_length=512, null=True, blank=True)
    build_id = models.CharField(max_length=255, null=True, blank=True)
    error_message = models.TextField(null=True, blank=True)
    
    # Sleep/wake management
    is_sleeping = models.BooleanField(default=False)
    desired_count = models.IntegerField(default=1)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name
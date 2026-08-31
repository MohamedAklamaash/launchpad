import secrets
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

    # Per-app HMAC secret for verifying GitHub push webhooks; null until owner generates one.
    github_webhook_secret = models.CharField(max_length=64, null=True, blank=True)

    class Meta:
        unique_together = [('infrastructure', 'name')]  # k8s object names derive from name alone
        indexes = [
            models.Index(fields=['user', 'infrastructure']),
            models.Index(fields=['status']),
        ]
    version = models.IntegerField(default=1)

    dockerfile_path = models.CharField(max_length=255, default="Dockerfile", blank=True)
    build_context = models.CharField(max_length=255, default="", blank=True, null=True)
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

    # Kubernetes object handles for EKS deploys; null for ECS (ARN columns above).
    runtime_refs = models.JSONField(null=True, blank=True)
    
    # Sleep/wake management
    is_sleeping = models.BooleanField(default=False)
    desired_count = models.IntegerField(default=1)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    def issue_webhook_secret(self) -> str:
        # 32 bytes of url-safe entropy keeps the secret short enough to paste into GitHub
        # while staying well above the 256-bit threshold for HMAC-SHA256 brute force.
        self.github_webhook_secret = secrets.token_urlsafe(32)
        self.save(update_fields=["github_webhook_secret"])
        return self.github_webhook_secret
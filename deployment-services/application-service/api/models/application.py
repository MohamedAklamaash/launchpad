import secrets

from django.conf import settings
from django.db import models
from shared.utils.uuid import uuid7_pk

from api.fields import EncryptedJSONField
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

    # Set by a rollback so the next push doesn't silently undo it. The webhook acknowledges
    # pushes without deploying while this is set; a manual deploy or the resume endpoint
    # clears it.
    auto_deploy_paused = models.BooleanField(default=False)

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

    # Fernet ciphertext at rest (H1) — see api/fields.py and migration
    # 0037_encrypt_application_envs. Every read decrypts, every write encrypts; nothing
    # about the Python-level dict interface changes.
    envs = EncryptedJSONField(default=dict, null=True, blank=True)
    metadata = models.JSONField(default=dict, null=True, blank=True)
    # Explicit opt-in list of Database (read-model) ids this app injects credentials
    # for. Attach does not auto-redeploy — the next deploy picks up the new set.
    attached_database_ids = models.JSONField(default=list, blank=True)
    
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
    # F1b part 3a: set by the most recent deploy that ran in host mode (ECS's 443
    # host-header forward rule — aws/alb.py:create_host_forward_rule). Null means the last
    # deploy ran in path mode, either because the infra wasn't TLS-ready yet or the app's
    # slug isn't a valid single DNS label (see api/common/host_url.py). Path URLs keep
    # working regardless — host mode is additive, never a migration.
    host_forward_rule_arn = models.CharField(max_length=512, null=True, blank=True)
    # EKS has no per-app ARN to persist (the host rule lives inside the app's own Ingress
    # manifest, reapplied by every deploy) — this is the equivalent boolean signal for the
    # host_url API gate.
    host_route_applied = models.BooleanField(default=False)
    deployment_url = models.CharField(max_length=512, null=True, blank=True)
    build_id = models.CharField(max_length=255, null=True, blank=True)
    error_message = models.TextField(null=True, blank=True)

    # H4: the CloudWatch log group this app's first deploy created, once it carries a
    # per-infra+per-app hash discriminator instead of the legacy `/ecs/{slug}-task`
    # name shared by any two infras whose apps happen to share a slug. Null for a row
    # that deployed before this field existed — those keep using the legacy name
    # forever (see api/common/naming.py:ecs_log_group_for), never backfilled, so a
    # currently-running app's logs are never split across two group names.
    log_group_name = models.CharField(max_length=512, null=True, blank=True)

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
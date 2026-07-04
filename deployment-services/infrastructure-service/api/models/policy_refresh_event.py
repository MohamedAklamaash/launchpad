from django.conf import settings
from django.db import models
from shared.utils.uuid import uuid7_pk


class PolicyRefreshEvent(models.Model):
    """Audit record for a customer-run IAM script execution: who ran it (resolved
    from the script API key), against which AWS account/role, and when."""

    id = models.UUIDField(primary_key=True, default=uuid7_pk, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='policy_refresh_events',
    )
    # SET_NULL: the audit trail must outlive the infrastructure row.
    infrastructure = models.ForeignKey(
        'api.Infrastructure',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='policy_refresh_events',
    )
    account_id = models.CharField(max_length=32)
    caller_arn = models.TextField(blank=True, default="")
    script = models.CharField(max_length=64, default="create_aws_role.sh")
    role_name = models.CharField(max_length=128, blank=True, default="")
    policy_arn = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['user', 'created_at'], name='pre_user_created_idx'),
        ]

from django.db import models
from shared.enums.user_role import UserRole
from shared.utils.uuid import uuid7_pk


class InfrastructureUserRole(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid7_pk, editable=False)
    infrastructure = models.ForeignKey(
        'api.Infrastructure',
        on_delete=models.CASCADE,
        related_name='user_roles',
    )
    user = models.ForeignKey(
        'api.User',
        on_delete=models.CASCADE,
        related_name='infrastructure_roles',
    )
    role = models.CharField(
        max_length=20,
        choices=UserRole.choices,
        default=UserRole.USER,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('infrastructure', 'user')]
        indexes = [
            models.Index(fields=['infrastructure', 'user'], name='api_infrast_infrast_idx'),
            models.Index(fields=['user', 'role'], name='api_infrast_user_id_idx'),
        ]

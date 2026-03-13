from django.db import models
from django.conf import settings
from shared.enums.user_role import UserRole
from shared.utils.uuid import uuid7_pk

class InfrastructureUserRole(models.Model):
    """Track user roles per infrastructure"""
    id = models.UUIDField(primary_key=True, default=uuid7_pk, editable=False)
    infrastructure = models.ForeignKey(
        'Infrastructure',
        on_delete=models.CASCADE,
        related_name='user_roles'
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='infrastructure_roles'
    )
    role = models.CharField(
        max_length=20,
        choices=UserRole.choices,
        default=UserRole.USER
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        unique_together = ('infrastructure', 'user')
        indexes = [
            models.Index(fields=['infrastructure', 'user']),
            models.Index(fields=['user', 'role']),
        ]
    
    def __str__(self):
        return f"{self.user.email} - {self.role} on {self.infrastructure.name}"

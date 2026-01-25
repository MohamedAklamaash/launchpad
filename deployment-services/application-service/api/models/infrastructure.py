from django.db import models
from django.conf import settings
from shared.enums.cloud_provider import CloudProvider
from shared.utils.uuid import uuid7_pk

class Infrastructure(models.Model):
    id = models.UUIDField(
        primary_key=True,
        default=uuid7_pk,
        editable=False,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='infrastructures',
    )
    cloud_provider = models.CharField(
        max_length=30,
        choices = CloudProvider.choices
    )
    max_cpu = models.FloatField()
    max_memory = models.FloatField()
    is_cloud_authenticated = models.BooleanField(default=False)
    code = models.TextField(null=True, blank=True) # some auth code from cloud provider
    metadata = models.JSONField(null=True, blank=True)
    invited_users = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name='invited_infrastructures',
        blank=True,
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

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
    version = models.IntegerField(default=1)

    build_command = models.CharField(max_length=255)
    start_command = models.CharField(max_length=255)
    install_command = models.CharField(max_length=255)

    envs = models.JSONField(default=dict, null=True, blank=True)
    metadata = models.JSONField(default=dict, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name
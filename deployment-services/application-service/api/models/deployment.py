from django.db import models
from shared.utils.uuid import uuid7_pk

from api.models.application import Application


class Deployment(models.Model):
    """Append-only deploy history. Never updated after creation — a rollback writes a new
    row rather than mutating the one it targets.

    This snapshots the SHAPE of the environment, never the values. `Application.envs`
    is plaintext, and a per-deploy copy of the values would turn one plaintext row into one
    per deploy forever, with no purge path and two new readers (rollback UI, exit export).
    `env_keys` + `env_values_hash` let a rollback preview show "these keys changed" and
    "values differ from what's live" without ever storing or exposing a value.
    """

    TAG_SOURCE_RESOLVED_SHA = "resolved_sha"
    TAG_SOURCE_LATEST = "latest"
    TAG_SOURCE_CHOICES = [
        (TAG_SOURCE_RESOLVED_SHA, "Resolved SHA"),
        (TAG_SOURCE_LATEST, "Latest (unpinned)"),
    ]

    STATUS_SUCCEEDED = "SUCCEEDED"
    STATUS_FAILED = "FAILED"
    STATUS_CHOICES = [(STATUS_SUCCEEDED, "Succeeded"), (STATUS_FAILED, "Failed")]

    TRIGGERED_BY_DEPLOY = "DEPLOY"
    TRIGGERED_BY_ROLLBACK = "ROLLBACK"
    TRIGGERED_BY_CHOICES = [(TRIGGERED_BY_DEPLOY, "Deploy"), (TRIGGERED_BY_ROLLBACK, "Rollback")]

    id = models.UUIDField(primary_key=True, default=uuid7_pk, editable=False)
    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="deployments")

    image_tag = models.CharField(max_length=255)
    commit_sha = models.CharField(max_length=255, null=True, blank=True)
    # Only a `resolved_sha` row is addressable by rollback: it names a tag the buildspec
    # will never overwrite. A `latest` row (a CodeBuild project that predates the two-tag
    # buildspec) has no fixed image behind it by the time anyone rolls back to it.
    tag_source = models.CharField(max_length=20, choices=TAG_SOURCE_CHOICES)
    compute_type = models.CharField(max_length=30)

    env_keys = models.JSONField(default=list)
    env_values_hash = models.CharField(max_length=64)
    attached_database_ids = models.JSONField(default=list)
    cpu = models.FloatField()
    memory = models.FloatField()
    port = models.IntegerField()

    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    triggered_by = models.CharField(max_length=20, choices=TRIGGERED_BY_CHOICES)
    rolled_back_from = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="rollbacks",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "deployments"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["application", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.application_id}:{self.image_tag}"

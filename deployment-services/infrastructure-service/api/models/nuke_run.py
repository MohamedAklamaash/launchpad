from django.db import models
from shared.utils.uuid import uuid7_pk

# Ordered (key, label) pairs for every step a nuke run tracks. Fixed order — the worker
# executes them in exactly this sequence and each one is independently idempotent, so a
# resumed run can skip any step already marked "success".
NUKE_STEP_DEFINITIONS = [
    ("apps", "Delete applications"),
    ("databases", "Delete pre-existing database snapshots"),
    ("terraform_teardown", "Tear down custom domains, EKS orphans, and Terraform-managed infrastructure (no final DB snapshot)"),
    ("leftovers", "Remove CodeBuild project/role, log groups, ECR repository, and task definitions"),
    ("shared_resources", "Remove or update shared account resources (Terraform state backend, deployment role)"),
    ("verify", "Verify nothing Launchpad created is left in the account"),
]


def initial_steps() -> list[dict]:
    return [
        {"key": key, "label": label, "status": "pending", "detail": None}
        for key, label in NUKE_STEP_DEFINITIONS
    ]


class NukeRun(models.Model):
    """One 'Nuke infrastructure' run.

    Deliberately NOT a ForeignKey to Infrastructure: a successful run's last act is
    deleting the Infrastructure/Environment/Application/Database rows (the same way
    run_destroy does for a plain delete), and this row must survive that so
    GET .../nuke/ can still answer with the final outcome. `infrastructure_id` is a
    plain UUID, `infrastructure_name`/`user_id` are snapshotted at start so both the
    status endpoint and its ownership check work with the Infrastructure row gone.
    """

    STATUS_CHOICES = [
        ("PENDING", "Pending"),
        ("RUNNING", "Running"),
        ("FAILED", "Failed"),
        ("COMPLETED", "Completed"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid7_pk, editable=False)
    infrastructure_id = models.UUIDField(db_index=True)
    infrastructure_name = models.CharField(max_length=255)
    user_id = models.UUIDField(db_index=True)
    confirm_name = models.CharField(max_length=255)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="PENDING")
    # [{"key": str, "label": str, "status": "pending"|"running"|"success"|"failed"|"policy_refresh_required", "detail": str|None}, ...]
    steps = models.JSONField(default=list)
    # [{"type": str, "id": str, "reason": str}, ...] — populated by the verify step;
    # cleared at the start of every attempt and recomputed fresh.
    leftovers = models.JSONField(default=list)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "nuke_runs"
        indexes = [
            models.Index(fields=["infrastructure_id", "created_at"], name="nuke_infra_created_idx"),
        ]

    def step(self, key: str) -> dict | None:
        return next((s for s in self.steps if s["key"] == key), None)

    def set_step(self, key: str, status: str, detail: str | None = None) -> None:
        for s in self.steps:
            if s["key"] == key:
                s["status"] = status
                s["detail"] = detail
                return
        self.steps.append({"key": key, "label": key, "status": status, "detail": detail})

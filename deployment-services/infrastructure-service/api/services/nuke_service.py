"""Owner-only "Nuke infrastructure" action: destroys every resource Launchpad created
for an infrastructure in the customer's AWS account, then verifies nothing is left.

This module is the HTTP-facing half (start/status, ownership, typed-name confirmation,
409 on an in-flight run). The actual ordered teardown runs on the infra worker queue —
see api/services/nuke_worker.py — behind the same per-infra Redis lock as provision/
destroy, so it can never race a terraform apply/destroy against the same account.

NukeRun is deliberately not a ForeignKey to Infrastructure (see its docstring): a
completed run's last act is deleting the Infrastructure/Environment/Application/
Database rows, and the status endpoint must still answer after that.
"""
import logging

from api.models.environment import Environment
from api.models.nuke_run import NukeRun, initial_steps
from api.services.infra_queue import InfraQueue
from api.services.infrastructure import InfrastructureService
from api.services.infrastructure_permissions import InfrastructurePermissions
from django.db import transaction

logger = logging.getLogger(__name__)
audit_logger = logging.getLogger("audit.nuke")

_IN_FLIGHT_STATUSES = ("PENDING", "RUNNING")


def _serialize(run: NukeRun) -> dict:
    return {
        "infrastructure_id": str(run.infrastructure_id),
        "status": run.status,
        "steps": run.steps,
        "leftovers": run.leftovers,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


class NukeService:
    def __init__(self):
        self.infra_service = InfrastructureService()
        self.repo = self.infra_service.repo

    def start_nuke(self, user_id, infra_id, confirm_name: str) -> dict | None:
        """Owner-only. Typed-name confirmation against the infra's current name. 409
        (ValueError) if a run is already PENDING/RUNNING, or if the environment has a
        terraform operation in flight. Resumes (reuses the same NukeRun row, keeping
        already-succeeded step statuses) if the most recent run for this infra FAILED —
        every step is idempotent, so a resumed run skips what already succeeded."""
        infra = self.repo.get_by_id(user_id, infra_id)
        if not infra:
            return None
        if not InfrastructurePermissions.can_delete_infrastructure(infra, user_id):
            raise PermissionError("Only the infrastructure owner can nuke it")
        # Mirrors reprovision's F6 gate: an exited infrastructure has told its owner
        # Launchpad is done touching its account. Nuking it would contradict that.
        if infra.exited_at is not None:
            raise ValueError("This infrastructure has exited and can no longer be nuked.")
        if not confirm_name or confirm_name != infra.name:
            raise ValueError("confirm_name does not match the infrastructure's name.")

        env = Environment.objects.filter(infrastructure_id=infra_id).first()
        if env and env.status in ('PROVISIONING', 'UPDATING', 'DESTROYING'):
            raise ValueError(f"Cannot nuke while status is {env.status}. Wait for the operation to complete.")

        existing = NukeRun.objects.filter(infrastructure_id=infra_id).order_by('-created_at').first()
        if existing and existing.status in _IN_FLIGHT_STATUSES:
            raise ValueError("A nuke run is already in progress for this infrastructure.")

        with transaction.atomic():
            if existing and existing.status == 'FAILED':
                run = existing
                run.status = 'PENDING'
                run.confirm_name = confirm_name
                run.finished_at = None
                run.leftovers = []
                run.save(update_fields=['status', 'confirm_name', 'finished_at', 'leftovers', 'updated_at'])
            else:
                run = NukeRun.objects.create(
                    infrastructure_id=infra_id,
                    infrastructure_name=infra.name,
                    user_id=user_id,
                    confirm_name=confirm_name,
                    status='PENDING',
                    steps=initial_steps(),
                )
            if env:
                env.status = 'DESTROYING'
                env.save(update_fields=['status'])
            else:
                Environment.objects.create(infrastructure=infra, status='DESTROYING')

        InfraQueue.enqueue_nuke(str(infra_id))
        audit_logger.info("nuke started", extra={
            "infrastructure_id": str(infra_id), "user_id": str(user_id), "nuke_run_id": str(run.id),
        })
        return _serialize(run)

    def get_status(self, user_id, infra_id) -> dict | None:
        """Owner-only. Falls back to the NukeRun's own stamped `user_id` for the
        ownership check once the Infrastructure row is gone (a completed nuke deletes
        it) — that field was set from an already-verified owner at start_nuke time."""
        infra = self.repo.get_by_id(user_id, infra_id)
        run = NukeRun.objects.filter(infrastructure_id=infra_id).order_by('-created_at').first()
        if not run:
            return None
        if infra:
            if not InfrastructurePermissions.can_delete_infrastructure(infra, user_id):
                raise PermissionError("Only the infrastructure owner can view its nuke status")
        elif str(run.user_id) != str(user_id):
            return None
        return _serialize(run)

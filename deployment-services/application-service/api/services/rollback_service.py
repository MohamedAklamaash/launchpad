import logging
import uuid

from shared.enums.user_role import UserRole

from api.models.deployment import Deployment
from api.repositories.application import ApplicationRepository
from api.repositories.infrastructure import InfrastructureRepository
from api.services.application_service import DeploymentInProgressError
from api.services.deployment_lock import DeploymentLock
from api.services.deployment_queue import DeploymentQueue
from api.services.deployment_snapshot import snapshot_env
from api.services.infrastructure_permissions import InfrastructurePermissions

logger = logging.getLogger(__name__)


class RollbackService:
    """Deployment history + rollback. Owner-only, matching the provisioning-logs endpoint:
    a stranger sees 404 (existence not leaked), an invited ADMIN sees 403 — only the
    infrastructure owner may view or trigger a rollback."""

    def __init__(self):
        self.app_repo = ApplicationRepository()
        self.infra_repo = InfrastructureRepository()

    def _get_owned_application(self, user_id, app_id):
        # A malformed id reaching a UUIDField filter raises django's ValidationError, not
        # ValueError/DoesNotExist — catch it here so it can't escape as an unhandled 500.
        try:
            uuid.UUID(str(app_id))
        except ValueError:
            raise LookupError("Application not found")

        app = self.app_repo.get_by_id(app_id)
        if not app:
            raise LookupError("Application not found")

        infra = self.infra_repo.get_infrastructure(app.infrastructure_id)
        role = InfrastructurePermissions.get_user_role(infra, user_id) if infra else None
        if role is None:
            raise LookupError("Application not found")
        if role != UserRole.SUPER_ADMIN:
            raise PermissionError("Only the infrastructure owner can view or roll back deployments")
        return app

    def _get_target(self, app, deployment_id) -> Deployment:
        try:
            uuid.UUID(str(deployment_id))
        except ValueError:
            raise LookupError("Deployment not found")
        try:
            # Only a resolved-SHA row is addressable: a `latest`-tag row (a CodeBuild
            # project that predates the two-tag buildspec) names a tag every later build
            # has since overwritten, so there is nothing fixed left to roll back to.
            return Deployment.objects.get(
                id=deployment_id, application=app, status=Deployment.STATUS_SUCCEEDED,
                tag_source=Deployment.TAG_SOURCE_RESOLVED_SHA,
            )
        except Deployment.DoesNotExist:
            raise LookupError("Deployment not found")

    def list_deployments(self, user_id, app_id):
        app = self._get_owned_application(user_id, app_id)
        return Deployment.objects.filter(application=app, status=Deployment.STATUS_SUCCEEDED)

    def get_rollback_preview(self, user_id, app_id, deployment_id) -> dict:
        app = self._get_owned_application(user_id, app_id)
        target = self._get_target(app, deployment_id)

        current_keys, current_hash = snapshot_env(app.envs or {})
        current_key_set = set(current_keys)
        target_key_set = set(target.env_keys)

        return {
            "deployment_id": str(target.id),
            "image_tag": target.image_tag,
            "commit_sha": target.commit_sha,
            "compute_type": target.compute_type,
            "cpu": target.cpu,
            "memory": target.memory,
            "port": target.port,
            "deployed_at": target.created_at,
            "added_keys": sorted(current_key_set - target_key_set),
            "removed_keys": sorted(target_key_set - current_key_set),
            # Never the values themselves — only whether they differ from what shipped.
            "values_changed": current_hash != target.env_values_hash,
        }

    def trigger_rollback(self, user_id, app_id, deployment_id) -> Deployment:
        app = self._get_owned_application(user_id, app_id)
        target = self._get_target(app, deployment_id)

        if DeploymentLock().is_locked(app_id):
            raise DeploymentInProgressError(
                "A deployment is already in progress for this application. Try again once it finishes."
            )

        # Pause before enqueueing: a push landing while the rollback job is still queued
        # must not redeploy over it once the worker gets to it.
        app.auto_deploy_paused = True
        app.save(update_fields=['auto_deploy_paused'])

        DeploymentQueue.enqueue_rollback(str(app.id), str(app.infrastructure_id), str(target.id))
        return target

    def resume_auto_deploy(self, user_id, app_id):
        app = self._get_owned_application(user_id, app_id)
        app.auto_deploy_paused = False
        app.save(update_fields=['auto_deploy_paused'])
        return app

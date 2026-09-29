"""Owner-only authorization ladder shared by the app-level read endpoints that reach into
the customer's own AWS/Kubernetes account (runtime logs, per-app metrics): resolve the
calling user's role on the application's infrastructure and find the environment/deployment
it would read from, raising the same {LookupError, PermissionError, NotDeployedError}
contract every such view maps to {404, 403, 409}.

Extracted from runtime_logs_service.py (F2) so a second endpoint with the identical "owner
only, app must exist and be deployed" shape doesn't duplicate the ladder — see
app_metrics_service.py, the second caller.
"""
import uuid

from shared.enums.orchestrator import ComputeType
from shared.enums.user_role import UserRole

from api.models.application import Application
from api.models.environment import Environment
from api.services.infrastructure_permissions import InfrastructurePermissions


class NotDeployedError(Exception):
    """The application has no active deployment to read from."""


def get_owner_authorized_app(user_id, app_id) -> Application:
    """LookupError for anything a non-member must not be able to distinguish from a
    nonexistent app (bad id, missing row, no membership at all); PermissionError only
    once membership is confirmed but the role isn't owner-level."""
    try:
        uuid.UUID(str(app_id))
    except ValueError:
        raise LookupError("Application not found") from None
    try:
        app = Application.objects.select_related("infrastructure").get(id=app_id)
    except Application.DoesNotExist:
        raise LookupError("Application not found") from None
    role = InfrastructurePermissions.get_user_role(app.infrastructure, user_id)
    if role is None:
        raise LookupError("Application not found")
    if role != UserRole.SUPER_ADMIN:
        raise PermissionError("Only the infrastructure owner can perform this action")
    return app


def get_deployed_environment(app: Application, infra) -> Environment:
    env = Environment.objects.filter(infrastructure_id=infra.id).first()
    if not env or env.status != "ACTIVE" or not env.cluster_arn:
        raise NotDeployedError("Environment is not active")
    is_eks = infra.compute_type == ComputeType.EKS
    deployed = (app.runtime_refs or {}).get("namespace") if is_eks else app.service_arn
    if not deployed:
        raise NotDeployedError("Application has not been deployed")
    return env

"""The one place application-service decides flag-on (ORGS_ENABLED) authorization.

Every view-layer permission check routes through `require_org_capability`: the resource's
organization must equal the token's `org_id` claim, the caller must hold an active
Membership in that org, and the role must be allowed the capability by
`shared.authz.org_matrix.MATRIX`. The request never supplies an org id; the only org inputs
are stored columns and the signed token claim.

Which column names a resource's org:
  * Infrastructure: its own `organization_id`.
  * Application: its infrastructure's `organization_id` (the infrastructure is the AssumeRole
    boundary, so it is authoritative). The application's own column must agree. A NULL or
    differing value is logged and the application is treated as not found.

Error contract (same split `owner_only.py` established): `LookupError` for anything a
non-member must not distinguish from a missing resource (wrong org, NULL org, no active
membership, internal disagreement), `PermissionError` once membership is confirmed but the
role is not allowed. The legacy `invited_users` M2M and `InfrastructureUserRole` are never
consulted here.
"""
import logging
import uuid

from django.conf import settings
from shared.authz.org_matrix import Capability, role_allows

from api.models.application import Application
from api.models.infrastructure import Infrastructure
from api.services.org_permissions import OrgPermissions

logger = logging.getLogger(__name__)

__all__ = [
    "Capability",
    "apps_visible_in_infra",
    "find_app_with_capability",
    "get_app_with_capability",
    "get_infra_with_capability",
    "has_app_capability",
    "has_org_capability",
    "orgs_enabled",
    "require_app_capability",
    "require_org_capability",
]

INSUFFICIENT_ROLE_MESSAGE = "Your organization role does not permit this action"


def orgs_enabled() -> bool:
    return bool(settings.ORGS_ENABLED)


def require_org_capability(user, resource, capability: Capability) -> str:
    org_id = getattr(resource, "organization_id", None)
    if org_id is None:
        logger.warning(
            "org authz: %s %s has no organization_id; refusing access",
            type(resource).__name__, getattr(resource, "pk", None),
        )
        raise LookupError("Resource not found")
    role = OrgPermissions.get_role(user, org_id)
    if role is None:
        raise LookupError("Resource not found")
    if not role_allows(role, capability):
        raise PermissionError(INSUFFICIENT_ROLE_MESSAGE)
    return role


def has_org_capability(user, resource, capability: Capability) -> bool:
    try:
        require_org_capability(user, resource, capability)
    except (LookupError, PermissionError):
        return False
    return True


def require_app_capability(user, app: Application, capability: Capability) -> str:
    infra = app.infrastructure
    if app.organization_id != infra.organization_id:
        logger.error(
            "org authz: application %s organization_id %s disagrees with infrastructure %s (%s); refusing access",
            app.id, app.organization_id, infra.id, infra.organization_id,
        )
        raise LookupError("Application not found")
    return require_org_capability(user, infra, capability)


def has_app_capability(user, app: Application, capability: Capability) -> bool:
    try:
        require_app_capability(user, app, capability)
    except (LookupError, PermissionError):
        return False
    return True


def get_infra_with_capability(user, infra_id, capability: Capability) -> Infrastructure:
    try:
        uuid.UUID(str(infra_id))
    except (ValueError, TypeError):
        raise LookupError("Infrastructure not found") from None
    infra = Infrastructure.objects.filter(id=infra_id).first()
    if infra is None:
        raise LookupError("Infrastructure not found")
    require_org_capability(user, infra, capability)
    return infra


def get_app_with_capability(user, app_id, capability: Capability, *, defer_envs: bool = False) -> Application:
    try:
        uuid.UUID(str(app_id))
    except (ValueError, TypeError):
        raise LookupError("Application not found") from None
    qs = Application.objects.select_related("infrastructure").filter(id=app_id)
    if defer_envs:
        qs = qs.defer("envs")
    app = qs.first()
    if app is None:
        raise LookupError("Application not found")
    require_app_capability(user, app, capability)
    return app


def find_app_with_capability(user, app_id, capability: Capability, *, defer_envs: bool = False):
    """`get_app_with_capability`, but a missing/out-of-scope app is None; an insufficient
    role still raises PermissionError."""
    try:
        return get_app_with_capability(user, app_id, capability, defer_envs=defer_envs)
    except LookupError:
        return None


def apps_visible_in_infra(user, infra_id):
    """Applications of an infrastructure the caller may view, as a queryset; empty when the
    infrastructure is out of scope. Only rows whose own org agrees with the infrastructure's
    are returned."""
    try:
        infra = get_infra_with_capability(user, infra_id, Capability.VIEW)
    except (LookupError, PermissionError):
        return Application.objects.none()
    return Application.objects.filter(
        infrastructure_id=infra.id, organization_id=infra.organization_id,
    ).defer("envs")

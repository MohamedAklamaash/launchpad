"""The one place infrastructure-service decides flag-on (ORGS_ENABLED) authorization.

Every view-layer permission check routes through `require_org_capability`: the resource's
own `organization_id` must equal the token's `org_id` claim, the caller must hold an active
Membership in that org, and the role must be allowed the capability by
`shared.authz.org_matrix.MATRIX`. The request never supplies an org id; the only org inputs
are the stored resource column and the signed token claim.

Error contract (same split `owner_only.py` established): `LookupError` for anything a
non-member must not distinguish from a missing resource (wrong org, NULL org, no active
membership), `PermissionError` once membership is confirmed but the role is not allowed.
The legacy `invited_users` M2M and `InfrastructureUserRole` are never consulted here.
"""
import logging
import uuid

from api.models.infrastructure import Infrastructure
from api.services.org_permissions import OrgPermissions
from django.conf import settings
from shared.authz.org_matrix import Capability, role_allows

logger = logging.getLogger(__name__)

__all__ = [
    "Capability",
    "find_infra_with_capability",
    "get_infra_with_capability",
    "has_org_capability",
    "org_infrastructures",
    "orgs_enabled",
    "require_org_capability",
    "require_token_org_capability",
    "token_org_id",
]

INSUFFICIENT_ROLE_MESSAGE = "Your organization role does not permit this action"


def orgs_enabled() -> bool:
    return bool(settings.ORGS_ENABLED)


def token_org_id(user):
    org_id = getattr(user, "org_id", None)
    return str(org_id) if org_id else None


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


def require_token_org_capability(user, capability: Capability) -> str:
    """For an action with no resource yet (creating an infrastructure): authorizes against
    the token's own org and returns it, so the caller can stamp it on the new row."""
    org_id = token_org_id(user)
    role = OrgPermissions.get_role(user, org_id) if org_id else None
    if role is None:
        raise LookupError("Organization not found")
    if not role_allows(role, capability):
        raise PermissionError(INSUFFICIENT_ROLE_MESSAGE)
    return org_id


def has_org_capability(user, resource, capability: Capability) -> bool:
    try:
        require_org_capability(user, resource, capability)
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


def find_infra_with_capability(user, infra_id, capability: Capability):
    """`get_infra_with_capability`, but a missing/out-of-scope infra is None (the legacy
    `repo.get_by_id` shape); an insufficient role still raises PermissionError."""
    try:
        return get_infra_with_capability(user, infra_id, capability)
    except LookupError:
        return None


def org_infrastructures(user):
    """Infrastructures in the token's org, or none at all when the caller holds no active
    Membership there. Rows with a NULL organization_id never match the equality filter."""
    org_id = token_org_id(user)
    if org_id is None or OrgPermissions.get_role(user, org_id) is None:
        return Infrastructure.objects.none()
    return Infrastructure.objects.filter(organization_id=org_id)

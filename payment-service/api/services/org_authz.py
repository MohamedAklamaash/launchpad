"""The one place payment-service decides flag-on (ORGS_ENABLED) authorization.

Billing is owner-only in the F9 matrix. The resource is the infrastructure a payment is
for: its `organization_id` must equal the token's `org_id` claim, the caller must hold an
active Membership in that org, and the role must be allowed the capability by
`shared.authz.org_matrix.MATRIX`. The request never supplies an org id.

Error contract: `LookupError` for anything a non-member must not distinguish from a
missing infrastructure (wrong org, NULL org, no active membership), `PermissionError`
once membership is confirmed but the role is not allowed.
"""
import logging
import uuid

from api.models.infrastructure import Infrastructure
from api.services.org_permissions import OrgPermissions
from django.conf import settings
from shared.authz.org_matrix import Capability, role_allows

logger = logging.getLogger(__name__)

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

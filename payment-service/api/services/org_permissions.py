import logging
import uuid

from api.models.membership import Membership
from django.conf import settings
from shared.services.org_role_cache import (
    evict_cached_role,
    get_cached_role,
    set_cached_role,
)

logger = logging.getLogger(__name__)


def _canonical_uuid(value):
    """`str(uuid.UUID(str(value)))` — lowercase, hyphenated, format-validated. Returns
    `None` for a malformed value rather than raising: every caller here treats "not a
    real UUID" the same as "doesn't match", never as a crash."""
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        return None


class OrgPermissions:
    """Role resolution for the F9 org model (plan/F9-teams-and-orgs.md, Decision: JWT
    and authorization), mirroring `InfrastructurePermissions.get_user_role`'s shape:
    a static method call sites use per-request, never a role carried on the JWT.

    `get_role` is the ONLY path a later view-permission rewrite (ticket 7) may use to
    decide a role — it never trusts `request.user.role`/`.roles` (the legacy claims)
    as authorization, and it never trusts the `org_id` a caller presents without first
    confirming the token's own `org_id` claim names the same org: the claim is a scope
    selector a signature already backs, not a role, and not a lookup key a caller can
    repoint to a different org than their own token was scoped to.

    Security-review follow-up (M1): earlier revisions of this method resolved a role
    for a claim-less token (no `account_id`) by walking the Organization mirror's
    `owner_account_id` for the caller's own personal org. Removed: a demoted/transferred
    owner (ticket 8's ownership transfer) stops being this cheap formula's target the
    moment ownership moves, and a stale cached/claim-less token would have kept
    resolving the OLD owner's role through this path until it expired. A token with no
    `account_id` claim at all now always resolves to `None` — the caller must re-login
    or refresh (which re-mints `account_id` from the row's current link) before any role
    resolves for them.
    """

    @staticmethod
    def get_role(user, org_id):
        if not settings.ORGS_ENABLED or not org_id:
            return None

        org_id = _canonical_uuid(org_id)
        if org_id is None:
            return None

        token_org_id = _canonical_uuid(getattr(user, "org_id", None))
        if not token_org_id or token_org_id != org_id:
            # The org_id claim is a scope selector, not a hint: a request may only ever
            # resolve a role for the org its own signed token names. Without this check,
            # a caller could pass an arbitrary org_id into a view and have this function
            # faithfully report a role in an org the token never scoped the request to —
            # turning the claim into decoration rather than a boundary.
            return None

        account_id = getattr(user, "account_id", None)
        if not account_id:
            # Security review M1 — see class docstring. No fallback: re-login/refresh
            # re-mints account_id, which is the only path back to a resolved role.
            return None

        return OrgPermissions._resolve_role(org_id, str(account_id))

    @staticmethod
    def _resolve_role(org_id, account_id):
        hit, cached_role = get_cached_role(org_id, account_id)
        if hit:
            return cached_role

        role = OrgPermissions._role_from_db(org_id, account_id)
        set_cached_role(org_id, account_id, role)
        return role

    @staticmethod
    def _role_from_db(org_id, account_id):
        # .active() excludes a tombstoned (removed_at IS NOT NULL) row — a removed
        # member is never a role, regardless of what their last-known row says.
        membership = Membership.objects.active().filter(
            organization_id=org_id, account_id=account_id,
        ).first()
        return membership.role if membership else None

    @staticmethod
    def evict(org_id, account_id) -> None:
        """Called by the org.events consumer right after it durably applies a
        Membership/Organization write — see shared.services.org_role_cache.evict_cached_role
        for the fail-closed/best-effort posture."""
        evict_cached_role(org_id, account_id)

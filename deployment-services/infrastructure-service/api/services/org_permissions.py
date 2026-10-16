import logging

from api.models.membership import Membership
from api.models.organization import Organization
from django.conf import settings
from shared.services.org_role_cache import (
    evict_cached_role,
    get_cached_role,
    set_cached_role,
)
from shared.utils.org_id import personal_org_id

logger = logging.getLogger(__name__)


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
    """

    @staticmethod
    def get_role(user, org_id):
        if not settings.ORGS_ENABLED or not org_id:
            return None

        org_id = str(org_id)
        token_org_id = getattr(user, "org_id", None)
        if not token_org_id or str(token_org_id) != org_id:
            # The org_id claim is a scope selector, not a hint: a request may only ever
            # resolve a role for the org its own signed token names. Without this check,
            # a caller could pass an arbitrary org_id into a view and have this function
            # faithfully report a role in an org the token never scoped the request to —
            # turning the claim into decoration rather than a boundary.
            return None

        account_id = getattr(user, "account_id", None)
        if not account_id:
            account_id = OrgPermissions._personal_org_owner_account_id(user, org_id)
            if not account_id:
                return None

        return OrgPermissions._resolve_role(org_id, str(account_id))

    @staticmethod
    def _personal_org_owner_account_id(user, org_id):
        """A token with no `account_id` claim at all — pre-F9 (minted before this
        ticket), or a credential row never linked to an Account — can still resolve a
        role, but ONLY for the caller's own personal org, computed independently of the
        claim (`org_id` is a pure function of the signature-covered legacy id). Resolved
        via the Organization mirror's `owner_account_id`, which was set to this exact
        account at personal-org creation (ticket 3) — never via any other org, which
        would let a claim-less token borrow a different org's owner identity."""
        sub = getattr(user, "id", None)
        if not sub:
            return None
        try:
            if org_id != str(personal_org_id(sub)):
                return None
        except (ValueError, TypeError):
            return None
        org = Organization.objects.filter(id=org_id).first()
        return org.owner_account_id if org else None

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

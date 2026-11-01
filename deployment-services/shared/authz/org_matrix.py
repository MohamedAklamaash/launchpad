"""The F9 permission matrix as data (plan/F9-teams-and-orgs.md, Decision: Role set and
permission matrix). Every flag-on authorization decision in infrastructure-service,
application-service and payment-service reads this table and nothing else; a cell changes
here or not at all.

The first 15 capabilities are the plan's rows verbatim. The `Members:*` rows are enforced
by auth-service and have no Django call site; they stay in the table so the matrix is one
complete, testable artifact. Owner decision 2026-11-01: only the owner manages admins, so
the admin cells in the `Members:*` rows cover developers and viewers only (the extra
admin-target rule lives in auth-service). The last two capabilities cover endpoints the plan's table
does not name. Each keeps the role set the endpoint had before F9 (owner-only, or
owner+admin for application deletion) so this ticket widens nothing the plan did not decide.
"""
from enum import Enum

from shared.enums.org_role import OrgRole


class Capability(str, Enum):
    VIEW = "view"
    CREATE_INFRASTRUCTURE = "create_infrastructure"
    DELETE_INFRASTRUCTURE = "delete_infrastructure"
    DEPLOY = "deploy"
    ROLLBACK = "rollback"
    LOGS_METRICS = "logs_metrics"
    COSTS = "costs"
    ENV_VARS = "env_vars"
    DATABASES = "databases"
    CUSTOM_DOMAINS = "custom_domains"
    EXIT = "exit"
    MEMBERS_INVITE = "members_invite"
    MEMBERS_REMOVE = "members_remove"
    MEMBERS_CHANGE_ROLE = "members_change_role"
    BILLING = "billing"
    DELETE_APPLICATION = "delete_application"
    MANAGE_INFRASTRUCTURE = "manage_infrastructure"


_OWNER = frozenset({OrgRole.OWNER.value})
_OWNER_ADMIN = _OWNER | {OrgRole.ADMIN.value}
_OWNER_ADMIN_DEV = _OWNER_ADMIN | {OrgRole.DEVELOPER.value}
_ALL = _OWNER_ADMIN_DEV | {OrgRole.VIEWER.value}

MATRIX: dict[Capability, frozenset[str]] = {
    Capability.VIEW: _ALL,
    Capability.CREATE_INFRASTRUCTURE: _OWNER_ADMIN,
    Capability.DELETE_INFRASTRUCTURE: _OWNER,
    Capability.DEPLOY: _OWNER_ADMIN_DEV,
    Capability.ROLLBACK: _OWNER_ADMIN_DEV,
    Capability.LOGS_METRICS: _OWNER_ADMIN_DEV,
    Capability.COSTS: _OWNER_ADMIN,
    Capability.ENV_VARS: _OWNER_ADMIN_DEV,
    Capability.DATABASES: _OWNER_ADMIN,
    Capability.CUSTOM_DOMAINS: _OWNER_ADMIN,
    Capability.EXIT: _OWNER,
    Capability.MEMBERS_INVITE: _OWNER_ADMIN,
    Capability.MEMBERS_REMOVE: _OWNER_ADMIN,
    Capability.MEMBERS_CHANGE_ROLE: _OWNER_ADMIN,
    Capability.BILLING: _OWNER,
    Capability.DELETE_APPLICATION: _OWNER_ADMIN,
    Capability.MANAGE_INFRASTRUCTURE: _OWNER,
}

PLAN_ROWS = tuple(Capability)[:15]


def role_allows(role, capability: Capability) -> bool:
    return role in MATRIX[capability]

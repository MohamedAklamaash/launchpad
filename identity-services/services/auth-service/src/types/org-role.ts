// F9's four-role matrix (plan/F9-teams-and-orgs.md, Decision: Role set and permission
// matrix), replacing the admin/user/guest-plus-implicit-super_admin/owner set USER_ROLE
// carries for the legacy per-infra model. Deliberately a distinct value set, not a reuse
// of USER_ROLE — Membership.role has its own rank order (owner is unique and DB-enforced,
// not just the top of the rank ladder) and its own transition rules.
export const ORG_ROLE = {
    OWNER: 'owner',
    ADMIN: 'admin',
    DEVELOPER: 'developer',
    VIEWER: 'viewer',
} as const;

export type OrgRole = (typeof ORG_ROLE)[keyof typeof ORG_ROLE];

export const ORG_ROLE_VALUES = Object.values(ORG_ROLE);

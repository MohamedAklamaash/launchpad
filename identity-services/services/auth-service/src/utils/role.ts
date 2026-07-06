import { USER_ROLE } from '@/types/auth.invited_user.types';

export const ROLE_RANK: Record<string, number> = {
    super_admin: 3,
    admin: 2,
    user: 1,
    guest: 0,
};

export const clampInvitedRole = (role: string): USER_ROLE =>
    role === USER_ROLE.SUPER_ADMIN ? USER_ROLE.ADMIN : (role as USER_ROLE);

export const primaryRole = (roles: Record<string, string>): USER_ROLE => {
    const values = Object.values(roles);
    if (!values.length) return USER_ROLE.USER;
    return values.reduce((best, r) =>
        (ROLE_RANK[r] ?? 0) > (ROLE_RANK[best] ?? 0) ? r : best,
    ) as USER_ROLE;
};

export interface RemovalAuthorization {
    toRemove: string[];
    error?: { status: number; message: string };
}

// A caller may remove a member only from infras the caller owns. An empty request means
// "every org I own that this member belongs to" — never all of the member's orgs, which
// would strip access to infras the caller does not control (and could delete their account).
export const authorizeMemberRemoval = (
    ownedInfras: string[],
    targetRoles: Record<string, string>,
    requestedInfraIds: string[],
): RemovalAuthorization => {
    const owned = new Set(ownedInfras);
    const unauthorized = requestedInfraIds.filter((infra) => !owned.has(infra));
    if (unauthorized.length) {
        return {
            toRemove: [],
            error: {
                status: 403,
                message: `Not authorized to remove members from: ${unauthorized.join(', ')}`,
            },
        };
    }
    const scope = requestedInfraIds.length ? requestedInfraIds : Object.keys(targetRoles);
    const toRemove = scope.filter((infra) => owned.has(infra) && Object.hasOwn(targetRoles, infra));
    if (!toRemove.length) {
        return {
            toRemove: [],
            error: { status: 404, message: 'Member is not in any organization you manage' },
        };
    }
    return { toRemove };
};

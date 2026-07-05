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

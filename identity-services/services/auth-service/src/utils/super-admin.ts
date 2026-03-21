import { User } from '@/db';
import { AccessTokenPayload } from './handle-token';
import { USER_ROLE } from '@/types/auth.invited_user.types';
import { HttpError } from '@launchpad/common';

export const superAdminMiddleware = async (payload: AccessTokenPayload) => {
    if (payload.role !== USER_ROLE.SUPER_ADMIN) {
        throw new HttpError(401, 'Unauthorized: Super Admin role required');
    }

    const user = await User.findOne({ where: { id: payload.sub } });
    if (!user) {
        throw new HttpError(401, 'Unauthorized: User not found');
    }

    return user;
};

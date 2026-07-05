import { BaseService } from '@/service/invited-users/invited-user.base.service';
import { InvitedUser, UserOTP, RefreshToken } from '@/db';
import { sequelize } from '@/db/sequalize';
import { hashPassword } from '@/utils/handle-password';
import { HttpError } from '@launchpad/common';
import { InvitedUserRegisterInput, USER_ROLE } from '@/types/auth.invited_user.types';
import { ROLE_RANK, clampInvitedRole, primaryRole } from '@/utils/role';
import {
    PublishUserRegistered,
    userAuthenticationQueue,
} from '@/messaging/producer/user-created.message';
import { AUTHENTICATE_INVITED_USER_EVENT } from '@launchpad/common';

export class InvitedUserService extends BaseService {
    public async listInvitedBy(inviterId: string) {
        return InvitedUser.findAll({
            where: { invited_by: inviterId },
            order: [['created_at', 'DESC']],
        });
    }

    // Remove a member from one or more orgs (infras). Rank is compared per-infra: the caller
    // must outrank the target's role IN that infra. The account is deleted only when this
    // leaves them in no orgs — otherwise they keep their account and their other orgs.
    public async removeFromOrg(
        callerId: string,
        callerRole: string,
        targetUserId: string,
        infraIds: string[],
    ) {
        return sequelize.transaction(async (transaction) => {
            const target = await InvitedUser.findByPk(targetUserId, {
                transaction,
                lock: transaction.LOCK.UPDATE,
            });
            if (!target) throw new HttpError(404, 'Member not found');

            if (target.invited_by !== callerId && callerRole !== USER_ROLE.SUPER_ADMIN) {
                throw new HttpError(403, 'You can only remove members you invited');
            }

            const nextRoles = { ...(target.roles ?? {}) };
            const toRemove = infraIds.length ? infraIds : Object.keys(nextRoles);

            for (const infra of toRemove) {
                const targetRole = nextRoles[infra] ?? target.role;
                if ((ROLE_RANK[callerRole] ?? 0) <= (ROLE_RANK[targetRole] ?? 0)) {
                    throw new HttpError(
                        403,
                        `A ${callerRole.replace('_', ' ')} cannot remove a ${targetRole} from this organization`,
                    );
                }
            }

            for (const infra of toRemove) delete nextRoles[infra];
            const remaining = Object.keys(nextRoles);

            if (remaining.length === 0) {
                await UserOTP.destroy({ where: { invited_user_id: target.id }, transaction });
                await RefreshToken.destroy({ where: { user_id: target.id }, transaction });
                await target.destroy({ transaction });
                return { removed: true, deleted_account: true };
            }

            target.roles = nextRoles;
            target.infra_id = remaining;
            target.role = primaryRole(nextRoles);
            await target.save({ transaction });
            return { removed: true, deleted_account: false };
        });
    }

    public async register(input: InvitedUserRegisterInput, super_user: string) {
        const { email, password, user_name, infra_id, role } = input;
        const invitedRole = clampInvitedRole(role);
        return sequelize.transaction(async (transaction) => {
            const existingUser = await InvitedUser.findOne({ where: { email }, transaction });
            const existingUserName = await InvitedUser.findOne({
                where: { user_name },
                transaction,
            });
            if (existingUserName && !existingUser) {
                throw new HttpError(409, 'Username already taken');
            }
            let user: InvitedUser;
            let requiresVerification = true;

            if (existingUser) {
                const nextRoles = { ...(existingUser.roles ?? {}) };
                const isNewInfra = !(infra_id in nextRoles);

                if (
                    !isNewInfra &&
                    existingUser.is_authenticated &&
                    nextRoles[infra_id] === invitedRole
                ) {
                    throw new HttpError(
                        409,
                        'User already registered and authenticated in this infra',
                    );
                }

                nextRoles[infra_id] = invitedRole;
                existingUser.roles = nextRoles;
                existingUser.infra_id = Object.keys(nextRoles);
                existingUser.role = primaryRole(nextRoles);
                if (isNewInfra) existingUser.is_authenticated = false;
                requiresVerification = isNewInfra || !existingUser.is_authenticated;
                user = await existingUser.save({ transaction });
            } else {
                const passwordHash = await hashPassword(password);
                user = await InvitedUser.create(
                    {
                        email,
                        user_name: user_name,
                        infra_id: [infra_id],
                        roles: { [infra_id]: invitedRole },
                        password_hash: passwordHash,
                        role: invitedRole,
                        is_authenticated: false,
                        invited_by: super_user,
                    },
                    { transaction },
                );
            }

            let otp: string | undefined;
            if (requiresVerification) {
                const otpRecord = await this.createOTP(user.id, infra_id, transaction);
                otp = otpRecord.otp;
                await userAuthenticationQueue.add(AUTHENTICATE_INVITED_USER_EVENT, {
                    user_id: user.id,
                    email,
                    otp: otpRecord.otp,
                    infra_id,
                    source: 'mail',
                    user_name,
                });
            }

            try {
                PublishUserRegistered({
                    id: user.id,
                    email,
                    user_name,
                    created_at: user.created_at,
                    infra_id: user.infra_id,
                    role: user.role,
                    roles: user.roles,
                    updated_at: user.updated_at,
                    metadata: {},
                    invited_by: super_user,
                });
            } catch (pubError) {
                console.error('Failed to publish user registered event', pubError);
            }

            return { user, otp };
        });
    }
}

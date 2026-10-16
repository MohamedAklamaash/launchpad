import { Transaction } from 'sequelize';
import { HttpError } from '@launchpad/common';
import { InvitedUser, Membership, RefreshToken, User } from '@/db';
import { sequelize } from '@/db/sequalize';
import { verifyRefreshToken } from '@/utils/handle-token';
import { assertOrgsEnabled } from './org-guard';
import { BaseService } from '@/service/invited-users/invited-user.base.service';

interface CredentialRow {
    id: string;
    email: string;
    user_name: string;
    role: string;
    roles?: Record<string, string>;
    infra_id: string[];
    created_at: Date;
    profile_url?: string;
    account_id: string | null;
}

// The caller may be either a GitHub OAuth owner (`User`) or a password account
// (`InvitedUser`) — switch-org has no reason to assume one or the other, unlike
// /auth/refresh, which today only ever looks up InvitedUser (a pre-existing gap, not
// something this ticket fixes). Tried in the same order superAdminMiddleware/upsertUser
// treat as canonical: User first, InvitedUser second.
const findCredentialRow = async (
    userId: string,
    transaction: Transaction,
): Promise<CredentialRow | null> => {
    const user = await User.findByPk(userId, { transaction });
    if (user) {
        return {
            id: user.id,
            email: user.email,
            user_name: user.user_name,
            role: user.role,
            infra_id: user.infra_id || [],
            created_at: user.created_at,
            profile_url: user.profile_url,
            account_id: user.account_id,
        };
    }

    const invitedUser = await InvitedUser.findByPk(userId, { transaction });
    if (invitedUser) {
        return {
            id: invitedUser.id,
            email: invitedUser.email,
            user_name: invitedUser.user_name,
            role: invitedUser.role,
            roles: invitedUser.roles,
            infra_id: invitedUser.infra_id || [],
            created_at: invitedUser.created_at,
            account_id: invitedUser.account_id,
        };
    }

    return null;
};

export interface SwitchOrgInput {
    // The caller's own id, read from a verified access token at the controller — never
    // trusted from the body. Cross-checked against the refresh token's own `sub` below so
    // the two credentials presented must agree on who is asking.
    callerId: string;
    refreshToken: string;
    organizationId: string;
}

// POST /api/auth/switch-org (plan/F9-teams-and-orgs.md, Decision: JWT and authorization).
// Verifies an ACTIVE Membership row for the caller in the target org — never the org_id
// claim alone, and never a tombstoned row — then rotates the refresh token (destroy +
// recreate, the same pattern /auth/refresh already uses) with `active_org_id` set to the
// target, and mints a fresh access token carrying the new `org_id`/`account_id`.
//
// The plan's own prose describes the request body as just `{organization_id}`; this
// service additionally requires the current refresh token so there is a concrete row to
// rotate `active_org_id` on — the access token alone carries no `tokenId` to look one up
// by. Documented here as a deliberate addition, not a deviation from the decision itself.
export class SwitchOrgService extends BaseService {
    public async switchOrg(input: SwitchOrgInput) {
        assertOrgsEnabled();

        const payload = verifyRefreshToken(input.refreshToken);
        if (payload.sub !== input.callerId) {
            // Never a distinguishing error — "wrong caller" and "stale/invalid token"
            // both read as the same 401 to avoid confirming which part failed.
            throw new HttpError(401, 'Invalid refresh token');
        }

        return sequelize.transaction(async (transaction) => {
            const tokenRecord = await RefreshToken.findOne({
                where: { token_id: payload.tokenId, user_id: payload.sub },
                transaction,
            });
            if (!tokenRecord) throw new HttpError(401, 'Invalid refresh token');

            const user = await findCredentialRow(payload.sub, transaction);
            if (!user) throw new HttpError(404, 'User not found');

            // No linked Account at all means no Membership can ever be found for this
            // caller in any org other than one nothing points back to them from — same
            // 404 as a genuine non-member, never a distinguishing error (H3's
            // no-distinguishing-403 rule, owner_only.py's LookupError convention).
            const membership = user.account_id
                ? await Membership.findOne({
                      // removed_at: null excludes a tombstoned row — a removed member is
                      // never treated as active just because the row still exists.
                      where: {
                          organization_id: input.organizationId,
                          account_id: user.account_id,
                          removed_at: null,
                      },
                      transaction,
                  })
                : null;
            if (!membership) {
                throw new HttpError(404, 'Organization not found');
            }

            await tokenRecord.destroy({ transaction });
            const newTokenRecord = await this.createRefreshToken(
                user.id,
                transaction,
                input.organizationId,
            );

            return this.buildAuthResponse(user, newTokenRecord.token_id, payload.auth_time, {
                accountId: user.account_id,
                orgId: input.organizationId,
            });
        });
    }
}

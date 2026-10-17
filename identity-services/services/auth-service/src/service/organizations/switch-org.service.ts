import { Transaction } from 'sequelize';
import { HttpError } from '@launchpad/common';
import { InvitedUser, Membership, RefreshToken, User } from '@/db';
import { sequelize } from '@/db/sequalize';
import { verifyRefreshToken } from '@/utils/handle-token';
import { canonicalizeUuid } from '@/utils/org-id';
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
    // Security review H1(b), revised under R1: a GitHub `User` row has no such column
    // (OAuth itself is the verification), so it's omitted — buildAuthResponse's
    // `!== null` check reads `undefined` as verified. An `InvitedUser` row carries its
    // own real `email_verified_at` (never `is_authenticated`, which register() resets
    // on every subsequent infra invite) — an inviter can create one with any email, and
    // it is not this person's identity until OTP verification proves it once, ever.
    email_verified_at?: Date | null;
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
            email_verified_at: invitedUser.email_verified_at,
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

        // Security review L1: canonicalized (lowercase, format-validated) before it's
        // used as a Membership lookup key or stamped into a minted token — two
        // differently-cased but equal UUIDs must never be treated as different scopes.
        // Thrown as a plain HttpError, not left to bubble as an uncaught Error: an
        // org_id that fails UUID validation is a bad request, not a server fault.
        let organizationId: string;
        try {
            organizationId = canonicalizeUuid(input.organizationId);
        } catch {
            throw new HttpError(400, 'organization_id must be a well-formed UUID');
        }

        const payload = verifyRefreshToken(input.refreshToken);
        if (payload.sub !== input.callerId) {
            // Never a distinguishing error — "wrong caller" and "stale/invalid token"
            // both read as the same 401 to avoid confirming which part failed.
            throw new HttpError(401, 'Invalid refresh token');
        }

        return sequelize.transaction(async (transaction) => {
            // Security review M2: locked for the transaction's lifetime — see
            // InvitedUserAuthService.refresh's identical fix for the full rationale. Two
            // concurrent switch-org calls on the same refresh token now serialize; the
            // loser's findOne sees nothing once the winner's transaction commits and
            // 401s, instead of both rotating the same token into two live chains.
            const tokenRecord = await RefreshToken.findOne({
                where: { token_id: payload.tokenId, user_id: payload.sub },
                transaction,
                lock: transaction.LOCK.UPDATE,
            });
            if (!tokenRecord) throw new HttpError(401, 'Invalid refresh token');

            const user = await findCredentialRow(payload.sub, transaction);
            if (!user) throw new HttpError(404, 'User not found');

            // Security review H1(b): an unverified InvitedUser can never be authorized
            // into ANY Membership, even one that (via a backfill-era email collision)
            // nominally points at their account_id — treated identically to having no
            // account_id at all, which is the same 404 a genuine non-member gets.
            const accountId = user.email_verified_at !== null ? user.account_id : null;

            // No linked Account at all means no Membership can ever be found for this
            // caller in any org other than one nothing points back to them from — same
            // 404 as a genuine non-member, never a distinguishing error (H3's
            // no-distinguishing-403 rule, owner_only.py's LookupError convention).
            const membership = accountId
                ? await Membership.findOne({
                      // removed_at: null excludes a tombstoned row — a removed member is
                      // never treated as active just because the row still exists.
                      where: {
                          organization_id: organizationId,
                          account_id: accountId,
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
                organizationId,
            );

            return this.buildAuthResponse(user, newTokenRecord.token_id, payload.auth_time, {
                accountId,
                orgId: organizationId,
            });
        });
    }
}

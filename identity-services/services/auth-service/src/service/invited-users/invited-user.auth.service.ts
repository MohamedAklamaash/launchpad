import { BaseService } from '@/service/invited-users/invited-user.base.service';
import { Account, InvitedUser, Membership, UserOTP, RefreshToken } from '@/db';
import { sequelize } from '@/db/sequalize';
import { comparePassword } from '@/utils/handle-password';
import { verifyRefreshToken } from '@/utils/handle-token';
import { personalOrgId } from '@/utils/org-id';
import { accountIdForEmail, normalizeEmail } from '@/utils/account-id';
import { env } from '@/config/env';
import { MAX_OTP_ATTEMPTS, NIL_INVITED_USER_ID, otpAttempts } from '@/utils/otp-attempts';
import { Op } from 'sequelize';
import { HttpError } from '@launchpad/common';
import { InvitedUserLoginInput, AuthenticateUserInput } from '@/types/auth.invited_user.types';
import { OTP_PURPOSE } from '@/types/otp-purpose';

export class InvitedUserAuthService extends BaseService {
    public async login(input: Omit<InvitedUserLoginInput, 'infra_id'>) {
        const { email, password } = input;
        return sequelize.transaction(async (transaction) => {
            const user = await InvitedUser.findOne({ where: { email }, transaction });
            if (!user) throw new HttpError(404, 'User not found');

            const isValid = await comparePassword(password, user.password_hash);
            if (!isValid) throw new HttpError(401, 'Invalid password');

            // Security review H1: an inviter can create this row with any email and a
            // password of their own choosing — it is not proven to belong to that email
            // until OTP verification completes. The pendingOtp check below only catches
            // this while the 10-minute OTP window is still open; once it expires,
            // is_authenticated stays false forever until the account actually verifies,
            // so this check (unconditional on OTP expiry) is the one that actually closes
            // the account-takeover window. Checked after the password, not before — an
            // unauthenticated caller must not be able to probe verification status
            // without first proving they know the password.
            if (!user.is_authenticated) {
                throw new HttpError(
                    401,
                    'Account not verified — check your email for the verification code',
                );
            }

            // Block login if there's a pending first-time OTP for any infra. Scoped to
            // the registration purpose specifically — an outstanding password-reset
            // code (a different flow, requested from an already-working account) must
            // not lock the owner out of their normal password login too. Mostly
            // redundant with the is_authenticated check above (register() resets
            // is_authenticated to false for a new infra invite, which already blocks
            // login), kept as defense in depth for the exact in-flight-OTP window.
            const pendingOtp = await UserOTP.findOne({
                where: {
                    invited_user_id: user.id,
                    purpose: OTP_PURPOSE.REGISTER,
                    expires_at: { [Op.gt]: new Date() },
                },
                transaction,
            });
            if (pendingOtp)
                throw new HttpError(
                    401,
                    'OTP pending — check your email to verify your account first',
                );

            const refreshToken = await this.createRefreshToken(user.id, transaction);
            return this.buildAuthResponse(user, refreshToken.token_id, undefined, {
                accountId: user.account_id,
                orgId: refreshToken.active_org_id,
            });
        });
    }

    public async authenticateWithOTP(input: AuthenticateUserInput) {
        const { email, otp } = input;
        const purpose = OTP_PURPOSE.REGISTER;

        // Claims a slot unconditionally, before the guess (or even whether the account
        // exists) is evaluated at all — see otp-attempts.ts. This one DB statement both
        // claims the slot AND is the only place that decides whether `email` is a real
        // account (H7: no separate InvitedUser.findOne up front), so an unknown email
        // and a known email run the exact same query with the exact same round trip
        // instead of one short-circuiting before the other. If N requests arrive
        // concurrently, each gets a distinct atomically-incremented count; only the
        // first MAX_OTP_ATTEMPTS can ever proceed past the cap check below, no matter
        // how they're interleaved.
        const claim = await otpAttempts.claimAttempt(email);

        if (claim && claim.attempts > MAX_OTP_ATTEMPTS) {
            // Outside any transaction on purpose: a 429 thrown inside a
            // sequelize.transaction callback rolls back everything that callback did,
            // including a destroy — this invalidation must actually commit.
            await UserOTP.destroy({ where: { invited_user_id: claim.userId, purpose } });
            throw new HttpError(429, 'Too many attempts — request a new code');
        }

        // H7 (R3 follow-up): an unknown email still runs the OTP lookup below, bound to
        // a row id that can never exist, instead of short-circuiting before ever
        // touching UserOTP — the same 400 an existing user gets for a wrong guess, and
        // now the same DB work behind it too.
        const lookupUserId = claim?.userId ?? NIL_INVITED_USER_ID;

        return sequelize.transaction(async (transaction) => {
            const otpRecord = await UserOTP.findOne({
                where: {
                    invited_user_id: lookupUserId,
                    otp,
                    purpose,
                    expires_at: { [Op.gt]: new Date() },
                },
                transaction,
            });
            if (!claim || !otpRecord) {
                throw new HttpError(400, 'Invalid or expired OTP');
            }

            await InvitedUser.update(
                { is_authenticated: true },
                { where: { id: claim.userId }, transaction },
            );
            await otpRecord.destroy({ transaction });
            await otpAttempts.resetAttempts(claim.userId);

            // Security review H1(c): this IS the verify hook — the exact moment this
            // credential row is first proven to own `claim.email`. Link it to an Account
            // now (same uuid5(ACCOUNT_NAMESPACE, normalized email) formula the backfill
            // script uses) rather than waiting for a later batch backfill to notice, so
            // a newly-verified user gets org features immediately. Guarded by
            // `account_id: null` so an already-linked row (a second verification call,
            // or one the backfill already reached) is never overwritten. find-or-create
            // the Account row itself first — a brand-new account's email may not have one
            // yet.
            let accountId = claim.accountId;
            if (!accountId) {
                accountId = accountIdForEmail(claim.email);
                await Account.findOrCreate({
                    where: { id: accountId },
                    defaults: {
                        id: accountId,
                        email: normalizeEmail(claim.email),
                        display_name: claim.user_name,
                    },
                    transaction,
                });
                await InvitedUser.update(
                    { account_id: accountId },
                    { where: { id: claim.userId, account_id: null }, transaction },
                );
            }

            const refreshToken = await this.createRefreshToken(claim.userId, transaction);
            return this.buildAuthResponse(
                {
                    id: claim.userId,
                    email: claim.email,
                    user_name: claim.user_name,
                    role: claim.role,
                    roles: claim.roles,
                    infra_id: claim.infra_id,
                    created_at: claim.created_at,
                    // Just verified above, in this same transaction — never anything
                    // but true here.
                    is_authenticated: true,
                },
                refreshToken.token_id,
                undefined,
                { accountId, orgId: refreshToken.active_org_id },
            );
        });
    }

    public async refresh(token: string) {
        const payload = verifyRefreshToken(token);
        return sequelize.transaction(async (transaction) => {
            // Security review M2: locked for the lifetime of this transaction. Two
            // concurrent refreshes racing the same token_id now serialize on this row —
            // the first to commit destroys it; the second's findOne (blocked until the
            // first transaction releases the lock) then sees nothing and 401s, instead
            // of both successfully rotating the same already-redeemed token into two
            // live chains.
            const tokenRecord = await RefreshToken.findOne({
                where: { token_id: payload.tokenId, user_id: payload.sub },
                transaction,
                lock: transaction.LOCK.UPDATE,
            });
            if (!tokenRecord) throw new HttpError(401, 'Invalid token');

            const user = await InvitedUser.findByPk(payload.sub, { transaction });
            if (!user) throw new HttpError(404, 'User not found');

            // Copied forward, never reset to the personal-org default — a refresh token
            // minted before this column existed has no active_org_id and gets stamped
            // with the personal org exactly once, on its first refresh after this change
            // ships (same one-time-grace shape as auth_time below); every refresh after
            // that carries the real value forward instead of silently undoing an org
            // switch on every rotation. Computed only when ORGS_ENABLED — see
            // createRefreshToken for why this must not run against a flag-off deployment.
            let activeOrgId = env.ORGS_ENABLED
                ? (tokenRecord.active_org_id ?? personalOrgId(user.id))
                : undefined;

            // Security review H3(a): a token scoped to a non-personal org must be
            // re-validated against the CANONICAL Membership table on every refresh, not
            // just trusted because it was valid when minted — otherwise a member removed
            // from an org keeps refreshing a token scoped to it for up to the refresh
            // token's own lifetime (30 days), far past the org_role cache's TTL. Skipped
            // for the personal org itself: that org's Membership row is guaranteed by
            // construction (ticket 2's createOrganization), and checking it here would
            // only add a query, not a security property.
            if (env.ORGS_ENABLED && activeOrgId) {
                const personalOrg = personalOrgId(user.id);
                if (activeOrgId !== personalOrg) {
                    const membership = user.account_id
                        ? await Membership.findOne({
                              where: {
                                  organization_id: activeOrgId,
                                  account_id: user.account_id,
                                  removed_at: null,
                              },
                              transaction,
                          })
                        : null;
                    if (!membership) activeOrgId = personalOrg;
                }
            }

            await tokenRecord.destroy({ transaction });
            const newTokenRecord = await this.createRefreshToken(user.id, transaction, activeOrgId);

            // Copied unchanged from the refresh token being redeemed, never reset to
            // `now` — a refresh proves possession of a refresh token, not a fresh
            // interactive login. A refresh token minted before this field existed has no
            // auth_time and gets stamped fresh exactly once, on its first refresh after
            // this change ships; every refresh after that carries the real original value
            // forward. That one-time grace window is bounded by the existing refresh-token
            // trust boundary (still requires a valid, unexpired, single-use refresh
            // token) — not a new bypass, just an unavoidable migration edge.
            return this.buildAuthResponse(user, newTokenRecord.token_id, payload.auth_time, {
                accountId: user.account_id,
                orgId: activeOrgId,
            });
        });
    }

    public async revokeRefreshTokensForUser(userId: string) {
        return sequelize.transaction(async (transaction) => {
            await RefreshToken.destroy({ where: { user_id: userId }, transaction });
            return true;
        });
    }
}

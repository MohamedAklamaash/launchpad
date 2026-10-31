import { BaseService } from '@/service/invited-users/invited-user.base.service';
import { InvitedUser, Membership, UserOTP, RefreshToken } from '@/db';
import { sequelize } from '@/db/sequalize';
import { randomBytes } from 'node:crypto';
import { comparePassword, hashPassword } from '@/utils/handle-password';
import { verifyRefreshToken } from '@/utils/handle-token';
import { personalOrgId } from '@/utils/org-id';
import { env } from '@/config/env';
import { MAX_OTP_ATTEMPTS, NIL_INVITED_USER_ID, otpAttempts } from '@/utils/otp-attempts';
import { Op, QueryTypes } from 'sequelize';
import { HttpError } from '@launchpad/common';
import { InvitedUserLoginInput, AuthenticateUserInput } from '@/types/auth.invited_user.types';
import { OTP_PURPOSE } from '@/types/otp-purpose';
import { linkAccountForVerifiedIdentity } from '@/service/account-link.service';

// The inviter chose the password on the row they created, so it cannot outlive first
// verification: it is replaced with the invitee's own, or with the hash of a random
// secret nobody holds (forgot-password is then the only way to get password login).
const passwordHashForFirstVerification = (newPassword?: string): Promise<string> =>
    hashPassword(newPassword ?? randomBytes(32).toString('hex'));

export class InvitedUserAuthService extends BaseService {
    public async login(input: Omit<InvitedUserLoginInput, 'infra_id'>) {
        const { email, password } = input;
        return sequelize.transaction(async (transaction) => {
            const user = await InvitedUser.findOne({ where: { email }, transaction });
            if (!user) throw new HttpError(404, 'User not found');

            const isValid = await comparePassword(password, user.password_hash);
            if (!isValid) throw new HttpError(401, 'Invalid password');

            // Security review H1, revised under R1: an inviter can create this row with
            // any email and a password of their own choosing — it is not proven to
            // belong to that email until OTP verification completes once, ever.
            // Deliberately gated on `email_verified_at`, NOT `is_authenticated` — the
            // latter is reset to `false` by register() on every subsequent infra
            // invite (see InvitedUserService.register), which an earlier revision of
            // this fix used as the gate and which locked an already-verified person
            // out of password login the moment they were invited anywhere else (R1:
            // "a production regression with the flag OFF" — this check runs
            // unconditionally, not behind ORGS_ENABLED, since it's a security fix, not
            // an org feature). `email_verified_at` is set once, the first time this
            // row's email is proven, and is never cleared again by a later invite.
            //
            // The old pending-OTP check that used to sit here (block login while a
            // REGISTER-purpose OTP is still outstanding) is gone: it was the SAME bug
            // from the other direction — register() mints a fresh REGISTER OTP on
            // every new infra invite, so an already-verified person with no
            // OTP-resend route and a second pending invite would have stayed locked
            // out forever once that OTP expired. email_verified_at is now the sole,
            // durable signal; a live REGISTER OTP for a new infra no longer blocks
            // login for an email that's already proven.
            if (!user.email_verified_at) {
                throw new HttpError(
                    401,
                    'Account not verified — check your email for the verification code',
                );
            }

            const refreshToken = await this.createRefreshToken(user.id, transaction);
            return this.buildAuthResponse(user, refreshToken.token_id, undefined, {
                accountId: user.account_id,
                orgId: refreshToken.active_org_id,
            });
        });
    }

    public async authenticateWithOTP(input: AuthenticateUserInput) {
        const { email, otp, newPassword } = input;
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
        const passwordHash = await passwordHashForFirstVerification(newPassword);

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

            // is_authenticated still flips true here (per-infra acceptance, unchanged
            // semantics) alongside email_verified_at, set ONCE via COALESCE so a second
            // verification call (or a row the migration backfill already reached)
            // never overwrites the original timestamp — see R1: email_verified_at is
            // the durable "this email was proven at least once" signal that
            // is_authenticated's per-infra resets must never affect once set.
            const [{ email_verified_at: emailVerifiedAt }] = await sequelize.query<{
                email_verified_at: Date;
            }>(
                `UPDATE invited_users
                    SET is_authenticated = true,
                        email_verified_at = COALESCE(email_verified_at, NOW()),
                        password_hash = CASE
                            WHEN email_verified_at IS NULL THEN :passwordHash
                            ELSE password_hash
                        END
                  WHERE id = :userId
              RETURNING email_verified_at`,
                {
                    replacements: { userId: claim.userId, passwordHash },
                    type: QueryTypes.SELECT,
                    transaction,
                },
            );
            await otpRecord.destroy({ transaction });
            await otpAttempts.resetAttempts(claim.userId);

            // Security review H1(c), tightened under R2: this IS the verify hook — the
            // exact moment this credential row is first proven to own `claim.email`.
            // Links it to an Account now rather than waiting for a later batch backfill
            // — but never merges into an Account a DIFFERENT credential row already
            // holds; see linkAccountForVerifiedIdentity for why that merge is a human
            // decision (the backfill's collision log), not a live-request one.
            const accountId =
                claim.accountId ??
                (await linkAccountForVerifiedIdentity({
                    email: claim.email,
                    displayName: claim.user_name,
                    table: 'invited_users',
                    rowId: claim.userId,
                    transaction,
                }));

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
                    email_verified_at: emailVerifiedAt,
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

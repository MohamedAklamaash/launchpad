import { BaseService } from '@/service/invited-users/invited-user.base.service';
import { InvitedUser, UserOTP, RefreshToken } from '@/db';
import { sequelize } from '@/db/sequalize';
import { comparePassword } from '@/utils/handle-password';
import { verifyRefreshToken } from '@/utils/handle-token';
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

            // Block login if there's a pending first-time OTP for any infra. Scoped to
            // the registration purpose specifically — an outstanding password-reset
            // code (a different flow, requested from an already-working account) must
            // not lock the owner out of their normal password login too.
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
            return this.buildAuthResponse(user, refreshToken.token_id);
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
                },
                refreshToken.token_id,
            );
        });
    }

    public async refresh(token: string) {
        const payload = verifyRefreshToken(token);
        return sequelize.transaction(async (transaction) => {
            const tokenRecord = await RefreshToken.findOne({
                where: { token_id: payload.tokenId, user_id: payload.sub },
                transaction,
            });
            if (!tokenRecord) throw new HttpError(401, 'Invalid token');

            const user = await InvitedUser.findByPk(payload.sub, { transaction });
            if (!user) throw new HttpError(404, 'User not found');

            await tokenRecord.destroy({ transaction });
            const newTokenRecord = await this.createRefreshToken(user.id, transaction);

            // Copied unchanged from the refresh token being redeemed, never reset to
            // `now` — a refresh proves possession of a refresh token, not a fresh
            // interactive login. A refresh token minted before this field existed has no
            // auth_time and gets stamped fresh exactly once, on its first refresh after
            // this change ships; every refresh after that carries the real original value
            // forward. That one-time grace window is bounded by the existing refresh-token
            // trust boundary (still requires a valid, unexpired, single-use refresh
            // token) — not a new bypass, just an unavoidable migration edge.
            return this.buildAuthResponse(user, newTokenRecord.token_id, payload.auth_time);
        });
    }

    public async revokeRefreshTokensForUser(userId: string) {
        return sequelize.transaction(async (transaction) => {
            await RefreshToken.destroy({ where: { user_id: userId }, transaction });
            return true;
        });
    }
}

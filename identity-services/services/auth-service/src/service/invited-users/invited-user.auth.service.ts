import { BaseService } from '@/service/invited-users/invited-user.base.service';
import { InvitedUser, UserOTP, RefreshToken } from '@/db';
import { sequelize } from '@/db/sequalize';
import { comparePassword } from '@/utils/handle-password';
import { verifyRefreshToken } from '@/utils/handle-token';
import { otpAttempts } from '@/utils/otp-attempts';
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

        // Looked up outside any transaction, before the attempt is claimed: the id (or
        // its absence) decides which Redis key this guess counts against, and — on a
        // 404 case — nothing here is written yet for a transaction to roll back.
        const user = await InvitedUser.findOne({ where: { email } });
        const attemptKey = otpAttempts.keyFor(user?.id, email);

        // Claims the slot unconditionally, before the guess (or even whether the
        // account exists) is evaluated at all — see otp-attempts.ts. If N requests
        // arrive concurrently, each gets a distinct atomic count; only the first 5 can
        // ever proceed past this line, no matter how they're interleaved.
        const allowed = await otpAttempts.claimAttempt(attemptKey);
        if (!allowed) {
            // Runs outside the transaction below on purpose: a 429 thrown inside a
            // sequelize.transaction callback rolls back everything that callback did,
            // including a destroy — this invalidation must actually commit.
            if (user) {
                await UserOTP.destroy({ where: { invited_user_id: user.id, purpose } });
            }
            throw new HttpError(429, 'Too many attempts — request a new code');
        }

        // Same response an existing user gets for a wrong code — a distinct "no such
        // account" response would let this endpoint enumerate emails independent of
        // ever guessing anything OTP-shaped.
        if (!user) throw new HttpError(400, 'Invalid or expired OTP');

        return sequelize.transaction(async (transaction) => {
            const otpRecord = await UserOTP.findOne({
                where: {
                    invited_user_id: user.id,
                    otp,
                    purpose,
                    expires_at: { [Op.gt]: new Date() },
                },
                transaction,
            });
            if (!otpRecord) {
                throw new HttpError(400, 'Invalid or expired OTP');
            }

            user.is_authenticated = true;
            await user.save({ transaction });
            await otpRecord.destroy({ transaction });
            await otpAttempts.clearAttempts(attemptKey);

            const refreshToken = await this.createRefreshToken(user.id, transaction);
            return this.buildAuthResponse(user, refreshToken.token_id);
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

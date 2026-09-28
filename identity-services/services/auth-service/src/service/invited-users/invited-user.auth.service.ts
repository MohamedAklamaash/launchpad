import { BaseService } from '@/service/invited-users/invited-user.base.service';
import { InvitedUser, UserOTP, RefreshToken } from '@/db';
import { sequelize } from '@/db/sequalize';
import { comparePassword } from '@/utils/handle-password';
import { verifyRefreshToken } from '@/utils/handle-token';
import { otpAttempts } from '@/utils/otp-attempts';
import { Op } from 'sequelize';
import { HttpError } from '@launchpad/common';
import { InvitedUserLoginInput, AuthenticateUserInput } from '@/types/auth.invited_user.types';

const REGISTRATION_OTP_PURPOSE = 'register';

export class InvitedUserAuthService extends BaseService {
    public async login(input: Omit<InvitedUserLoginInput, 'infra_id'>) {
        const { email, password } = input;
        return sequelize.transaction(async (transaction) => {
            const user = await InvitedUser.findOne({ where: { email }, transaction });
            if (!user) throw new HttpError(404, 'User not found');

            const isValid = await comparePassword(password, user.password_hash);
            if (!isValid) throw new HttpError(401, 'Invalid password');

            // Block login if there's a pending first-time OTP for any infra
            const pendingOtp = await UserOTP.findOne({
                where: { invited_user_id: user.id, expires_at: { [Op.gt]: new Date() } },
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

        return sequelize.transaction(async (transaction) => {
            const user = await InvitedUser.findOne({ where: { email }, transaction });
            if (!user) throw new HttpError(404, 'User not found');

            if (!(await otpAttempts.hasAttemptsRemaining(REGISTRATION_OTP_PURPOSE, email))) {
                // The cap is hit — invalidate whatever OTP is outstanding so the
                // attacker's next guess (even a correct one) can't land, and the
                // legitimate user has to request a fresh code.
                await UserOTP.destroy({ where: { invited_user_id: user.id }, transaction });
                throw new HttpError(429, 'Too many attempts — request a new code');
            }

            const otpRecord = await UserOTP.findOne({
                where: { invited_user_id: user.id, otp, expires_at: { [Op.gt]: new Date() } },
                transaction,
            });
            if (!otpRecord) {
                await otpAttempts.recordFailedAttempt(REGISTRATION_OTP_PURPOSE, email);
                throw new HttpError(400, 'Invalid or expired OTP');
            }

            user.is_authenticated = true;
            await user.save({ transaction });
            await otpRecord.destroy({ transaction });
            await otpAttempts.clearAttempts(REGISTRATION_OTP_PURPOSE, email);

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

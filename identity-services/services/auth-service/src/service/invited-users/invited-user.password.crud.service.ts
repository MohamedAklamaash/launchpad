import { BaseService } from '@/service/invited-users/invited-user.base.service';
import { InvitedUser, UserOTP, PasswordSettings } from '@/db';
import { sequelize } from '@/db/sequalize';
import { hashPassword, comparePassword } from '@/utils/handle-password';
import { signAccessToken, verifyAccessToken } from '@/utils/handle-token';
import { otpAttempts } from '@/utils/otp-attempts';
import { Op } from 'sequelize';
import { HttpError, FORGOT_PASSWORD_EVENT } from '@launchpad/common';
import {
    InvitedUserForgotPasswordInput,
    InvitedUserVerifyResetOtpInput,
    InvitedUserResetPasswordInput,
    InvitedUserUpdatePasswordInput,
} from '@/types/auth.invited_user.types';
import { userAuthenticationQueue } from '@/messaging/producer/user-created.message';

const RESET_OTP_PURPOSE = 'password-reset';

export class PasswordService extends BaseService {
    // Never returns the OTP, and never distinguishes "no such account" / "account has
    // no infra" from success — the caller (ForgotPassword controller) responds
    // identically either way, so this method's only observable effect for an unknown
    // email is that no email gets sent. Doing that here (rather than in the controller)
    // keeps the secret from ever crossing back over the HTTP boundary at all.
    public async requestPasswordReset(input: InvitedUserForgotPasswordInput): Promise<void> {
        const { email, infra_id } = input;
        await sequelize.transaction(async (transaction) => {
            const user = await InvitedUser.findOne({ where: { email }, transaction });
            if (!user) return;

            const targetInfraId = infra_id || user.infra_id[0];
            if (!targetInfraId) return;

            const otp = await this.createOTP(user.id, targetInfraId, transaction);

            await userAuthenticationQueue.add(FORGOT_PASSWORD_EVENT, {
                user_id: user.id,
                email,
                otp: otp.otp,
                infra_id: targetInfraId,
                source: 'forgot-password',
                user_name: user.user_name,
            });
        });
    }

    public async verifyResetOTP(input: InvitedUserVerifyResetOtpInput) {
        const { email, otp } = input;
        return sequelize.transaction(async (transaction) => {
            const user = await InvitedUser.findOne({ where: { email }, transaction });
            if (!user) throw new HttpError(404, 'User not found');

            if (!(await otpAttempts.hasAttemptsRemaining(RESET_OTP_PURPOSE, email))) {
                await UserOTP.destroy({ where: { invited_user_id: user.id }, transaction });
                throw new HttpError(429, 'Too many attempts — request a new code');
            }

            const otpRecord = await UserOTP.findOne({
                where: { invited_user_id: user.id, otp, expires_at: { [Op.gt]: new Date() } },
                transaction,
            });
            if (!otpRecord) {
                await otpAttempts.recordFailedAttempt(RESET_OTP_PURPOSE, email);
                throw new HttpError(400, 'Invalid or expired OTP');
            }

            await otpRecord.destroy({ transaction });
            await otpAttempts.clearAttempts(RESET_OTP_PURPOSE, email);

            // Return a short-lived reset token specifically for password reset
            return signAccessToken(
                {
                    sub: user.id,
                    email: user.email,
                    scope: 'password_reset',
                    user_name: user.user_name,
                    role: user.role,
                },
                '5m',
            );
        });
    }

    public async resetPassword(input: InvitedUserResetPasswordInput) {
        const { reset_token: resetToken, new_password: newPassword } = input;

        const payload = verifyAccessToken(resetToken);
        if (!payload || payload.scope !== 'password_reset')
            throw new HttpError(401, 'Invalid reset token');

        return sequelize.transaction(async (transaction) => {
            const user = await InvitedUser.findByPk(payload.sub, { transaction });
            if (!user) throw new HttpError(404, 'User not found');

            user.password_hash = await hashPassword(newPassword);
            user.forgot_password = false;
            await user.save({ transaction });

            const expiresAt = new Date();
            expiresAt.setDate(expiresAt.getDate() + 30);
            await PasswordSettings.upsert(
                { invited_user_id: user.id, expires_at: expiresAt },
                { transaction },
            );

            return true;
        });
    }

    public async updatePassword(input: InvitedUserUpdatePasswordInput) {
        const { user_id: userId, old_password: oldPassword, new_password: newPassword } = input;
        return sequelize.transaction(async (transaction) => {
            const user = await InvitedUser.findByPk(userId, { transaction });
            if (!user) throw new HttpError(404, 'User not found');

            const valid = await comparePassword(oldPassword, user.password_hash);
            if (!valid) throw new HttpError(401, 'Invalid current password');

            user.password_hash = await hashPassword(newPassword);
            await user.save({ transaction });

            const expiresAt = new Date();
            expiresAt.setDate(expiresAt.getDate() + 30);
            await PasswordSettings.upsert(
                { invited_user_id: user.id, expires_at: expiresAt },
                { transaction },
            );

            return true;
        });
    }

    public async isPasswordExpired(userId: string) {
        const settings = await PasswordSettings.findOne({ where: { invited_user_id: userId } });
        if (!settings) return false;
        return settings.expires_at.getTime() < Date.now();
    }
}

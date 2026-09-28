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
import { OTP_PURPOSE } from '@/types/otp-purpose';

export class PasswordService extends BaseService {
    // Never returns the OTP, and never distinguishes "no such account" / "account has
    // no infra" / "throttled" from success — the caller (ForgotPassword controller)
    // responds identically either way, so this method's only observable effect for an
    // unknown or throttled email is that no email gets sent. Doing that here (rather
    // than in the controller) keeps the secret from ever crossing back over the HTTP
    // boundary at all.
    public async requestPasswordReset(input: InvitedUserForgotPasswordInput): Promise<void> {
        const { email, infra_id } = input;

        // Bounds how often a new code can be minted at all — separate from the
        // guess-attempt cap below, which only bounds guesses against a code that
        // already exists. Without this, nothing stops repeated calls from stockpiling
        // outstanding codes (each one replacing the last, since createOTP dedupes by
        // purpose) or spamming the target's inbox. Fails open on a Redis error: this
        // runs inside ForgotPassword's fire-and-forget call, so an exception here would
        // silently abort every reset request (not just throttled ones) for as long as
        // Redis is unavailable — worse than the throttle it's meant to enforce, since
        // the response is identical either way and an outage isn't something an
        // attacker can trigger to their own advantage.
        try {
            if (await otpAttempts.shouldThrottleForgotPassword(email)) return;
        } catch (error) {
            console.error('shouldThrottleForgotPassword check failed, proceeding', error);
        }

        await sequelize.transaction(async (transaction) => {
            const user = await InvitedUser.findOne({ where: { email }, transaction });
            if (!user) return;

            const targetInfraId = infra_id || user.infra_id[0];
            if (!targetInfraId) return;

            const otp = await this.createOTP(
                user.id,
                targetInfraId,
                OTP_PURPOSE.PASSWORD_RESET,
                transaction,
            );

            // See inviter-user.crud.service.ts's equivalent call — the payload carries
            // the OTP in plaintext, so it must not linger in Redis after the consumer
            // has used it.
            await userAuthenticationQueue.add(
                FORGOT_PASSWORD_EVENT,
                {
                    user_id: user.id,
                    email,
                    otp: otp.otp,
                    infra_id: targetInfraId,
                    source: 'forgot-password',
                    user_name: user.user_name,
                },
                { removeOnComplete: true, removeOnFail: { age: 3600 } },
            );
        });
    }

    public async verifyResetOTP(input: InvitedUserVerifyResetOtpInput) {
        const { email, otp } = input;
        const purpose = OTP_PURPOSE.PASSWORD_RESET;

        // Same shape as InvitedUserAuthService.authenticateWithOTP: the slot is claimed
        // unconditionally, before the guess (or account existence) is evaluated, and
        // any cap-triggered invalidation runs outside the transaction below so it can
        // never be rolled back by the 429 it precedes.
        const user = await InvitedUser.findOne({ where: { email } });
        const attemptKey = otpAttempts.keyFor(user?.id, email);

        const allowed = await otpAttempts.claimAttempt(attemptKey);
        if (!allowed) {
            if (user) {
                await UserOTP.destroy({ where: { invited_user_id: user.id, purpose } });
            }
            throw new HttpError(429, 'Too many attempts — request a new code');
        }

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

            await otpRecord.destroy({ transaction });
            await otpAttempts.clearAttempts(attemptKey);

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

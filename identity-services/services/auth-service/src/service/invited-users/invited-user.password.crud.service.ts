import { BaseService } from "@/service/invited-users/invited-user.base.service";
import { InvitedUser, UserOTP, PasswordSettings } from "@/db";
import { sequelize } from "@/db/sequalize";
import { hashPassword, comparePassword } from "@/utils/handle-password";
import { signAccessToken, verifyAccessToken } from "@/utils/handle-token";
import { Op } from "sequelize";
import { HttpError } from "@launchpad/common";
import { InvitedUserForgotPasswordInput, InvitedUserVerifyResetOtpInput, InvitedUserResetPasswordInput, InvitedUserUpdatePasswordInput } from "@/types/auth.invited_user.types";

export class PasswordService extends BaseService {

    public async requestPasswordReset(input: InvitedUserForgotPasswordInput) {
        const { email, infra_id } = input;
        return sequelize.transaction(async (transaction) => {
            const user = await InvitedUser.findOne({ where: { email }, transaction });
            if (!user) throw new HttpError(404, "User not found");

            const targetInfraId = infra_id || user.infra_id[0];
            if (!targetInfraId) throw new HttpError(400, "User belongs to no infra");

            const otp = await this.createOTP(user.id, targetInfraId, transaction);
            // TODO: In a real app, send this OTP via email instead of returning it
            return otp.otp;
        });
    }

    public async verifyResetOTP(input: InvitedUserVerifyResetOtpInput) {
        const { email, otp } = input;
        return sequelize.transaction(async (transaction) => {
            const user = await InvitedUser.findOne({ where: { email }, transaction });
            if (!user) throw new HttpError(404, "User not found");

            const otpRecord = await UserOTP.findOne({
                where: { invited_user_id: user.id, otp, expires_at: { [Op.gt]: new Date() } },
                transaction
            });
            if (!otpRecord) throw new HttpError(400, "Invalid or expired OTP");

            await otpRecord.destroy({ transaction });

            // Return a short-lived reset token specifically for password reset
            return signAccessToken({ sub: user.id, email: user.email, scope: "password_reset" }, "5m");
        });
    }

    public async resetPassword(input: InvitedUserResetPasswordInput) {
        const { reset_token: resetToken, new_password: newPassword } = input;

        // In a real implementation we should check the scope inside the token
        const payload = verifyAccessToken(resetToken);
        if (!payload || payload.scope !== "password_reset") throw new HttpError(401, "Invalid reset token");

        return sequelize.transaction(async (transaction) => {
            const user = await InvitedUser.findByPk(payload.sub, { transaction });
            if (!user) throw new HttpError(404, "User not found");

            user.password_hash = await hashPassword(newPassword);
            user.forgot_password = false;
            await user.save({ transaction });

            const expiresAt = new Date();
            expiresAt.setDate(expiresAt.getDate() + 30);
            await PasswordSettings.upsert({ invited_user_id: user.id, expires_at: expiresAt }, { transaction });

            return true;
        });
    }

    public async updatePassword(input: InvitedUserUpdatePasswordInput) {
        const { email, old_password: oldPassword, new_password: newPassword } = input;
        return sequelize.transaction(async (transaction) => {
            const user = await InvitedUser.findOne({ where: { email }, transaction });
            if (!user) throw new HttpError(404, "User not found");

            const valid = await comparePassword(oldPassword, user.password_hash);
            if (!valid) throw new HttpError(401, "Invalid current password");

            user.password_hash = await hashPassword(newPassword);
            await user.save({ transaction });

            const expiresAt = new Date();
            expiresAt.setDate(expiresAt.getDate() + 30);
            await PasswordSettings.upsert({ invited_user_id: user.id, expires_at: expiresAt }, { transaction });

            return true;
        });
    }

    public async isPasswordExpired(userId: string) {
        const settings = await PasswordSettings.findOne({ where: { invited_user_id: userId } });
        if (!settings) return false;
        return settings.expires_at.getTime() < Date.now();
    }
}

import { BaseService } from "@/service/invited-users/invited-user.base.service";
import { InvitedUser, UserOTP, PasswordSettings } from "@/db";
import { sequelize } from "@/db/sequalize";
import { hashPassword, comparePassword } from "@/utils/handle-password";
import { Op } from "sequelize";
import { HttpError } from "@launchpad/common";

export class PasswordService extends BaseService {

    public async requestPasswordReset(email: string, infraId: string) {
        return sequelize.transaction(async (transaction) => {
            const user = await InvitedUser.findOne({ where: { email }, transaction });
            if (!user) throw new HttpError(404, "User not found");

            const otp = await this.createOTP(user.id, infraId, transaction);
            return otp.otp;
        });
    }

    public async verifyResetOTP(userId: string, infraId: string, otp: string) {
        return sequelize.transaction(async (transaction) => {
            const otpRecord = await UserOTP.findOne({
                where: { invited_user_id: userId, infra_id: infraId, otp, expires_at: { [Op.gt]: new Date() } },
                transaction
            });
            if (!otpRecord) throw new HttpError(400, "Invalid or expired OTP");

            await otpRecord.destroy({ transaction });
            return true;
        });
    }

    public async resetPassword(userId: string, newPassword: string) {
        return sequelize.transaction(async (transaction) => {
            const user = await InvitedUser.findByPk(userId, { transaction });
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

    public async updatePassword(userId: string, oldPassword: string, newPassword: string) {
        return sequelize.transaction(async (transaction) => {
            const user = await InvitedUser.findByPk(userId, { transaction });
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

import { InvitedUserService } from "@/service/invited-users/inviter-user.crud.service";
import { PasswordService } from "@/service/invited-users/invited-user.password.crud.service";
import { InvitedUserAuthService } from "@/service/invited-users/invited-user.auth.service";

export class InvitedUserFacade {
    private userService = new InvitedUserService();
    private passwordService = new PasswordService();
    private authService = new InvitedUserAuthService();

    public async register(email: string, password: string, userName: string, infraId: string, role: string) {
        const user = await this.userService.register(email, password, userName, infraId, role);
        const otp = await this.passwordService.requestPasswordReset(email, infraId);
        return { user, otp };
    }

    public async login(email: string, password: string, infraId: string) {
        return this.authService.login(email, password, infraId);
    }

    public async authenticateWithOTP(userId: string, otp: string, infraId: string) {
        return this.authService.authenticateWithOTP(userId, otp, infraId);
    }

    public async forgotPassword(email: string, infraId: string) {
        return this.passwordService.requestPasswordReset(email, infraId);
    }

    public async verifyResetOTP(userId: string, infraId: string, otp: string) {
        return this.passwordService.verifyResetOTP(userId, infraId, otp);
    }

    public async resetPassword(userId: string, newPassword: string) {
        return this.passwordService.resetPassword(userId, newPassword);
    }

    public async updatePassword(userId: string, oldPassword: string, newPassword: string) {
        return this.passwordService.updatePassword(userId, oldPassword, newPassword);
    }

    public async isPasswordExpired(userId: string) {
        return this.passwordService.isPasswordExpired(userId);
    }

    public async refresh(token: string) {
        return this.authService.refresh(token);
    }

    public async revokeRefreshToken(userId: string) {
        return this.authService.revokeRefreshTokensForUser(userId);
    }
}

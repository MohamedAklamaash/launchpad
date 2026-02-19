import { InvitedUserService } from "@/service/invited-users/inviter-user.crud.service";
import { PasswordService } from "@/service/invited-users/invited-user.password.crud.service";
import { InvitedUserAuthService } from "@/service/invited-users/invited-user.auth.service";
import {
    InvitedUserRegisterInput,
    InvitedUserLoginInput,
    AuthenticateUserInput,
    InvitedUserForgotPasswordInput,
    InvitedUserVerifyResetOtpInput,
    InvitedUserResetPasswordInput,
    InvitedUserUpdatePasswordInput
} from "@/types/auth.invited_user.types";

export class InvitedUserFacade {
    private userService = new InvitedUserService();
    private passwordService = new PasswordService();
    private authService = new InvitedUserAuthService();

    public async register(input: InvitedUserRegisterInput, super_user: string) {
        const { user, otp } = await this.userService.register(input, super_user);
        return { user, otp };
    }

    public async login(input: InvitedUserLoginInput) {
        return this.authService.login(input);
    }

    public async authenticateWithOTP(input: AuthenticateUserInput) {
        return this.authService.authenticateWithOTP(input);
    }

    public async forgotPassword(input: InvitedUserForgotPasswordInput) {
        return this.passwordService.requestPasswordReset(input);
    }

    public async verifyResetOTP(input: InvitedUserVerifyResetOtpInput) {
        return this.passwordService.verifyResetOTP(input);
    }

    public async resetPassword(input: InvitedUserResetPasswordInput) {
        return this.passwordService.resetPassword(input);
    }

    public async updatePassword(input: InvitedUserUpdatePasswordInput) {
        return this.passwordService.updatePassword(input);
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


export enum USER_ROLE {
    ADMIN = "admin",
    USER = "user",
    GUEST = "guest"
}

export interface InvitedUserRegisterInput {
    infra_id: string;
    email: string;
    user_name: string;
    password: string;
    role: USER_ROLE;
}

export interface InvitedUserLoginInput {
    email: string;
    password: string;
    infra_id: string; // a user can belong to multiple infras but they can join into an infra one by one
}

export interface AuthenticateUserInput {
    email: string;
    otp: string;
}

export interface UserData {
    id: string;
    email: string;
    user_name: string;
    role: USER_ROLE;
    infra_id: string[];
    createdAt: string;
}

export interface AuthTokens {
    accessToken: string;
    refreshToken: string;
}

export interface AuthResponse extends AuthTokens {
    user: UserData;
}

export interface InvitedUserForgotPasswordInput {
    email: string;
    infra_id?: string;
}

export interface InvitedUserVerifyResetOtpInput {
    email: string;
    otp: string;
}

export interface InvitedUserResetPasswordInput {
    reset_token: string;
    new_password: string;
}

export interface InvitedUserUpdatePasswordInput {
    email: string;
    old_password: string;
    new_password: string;
}
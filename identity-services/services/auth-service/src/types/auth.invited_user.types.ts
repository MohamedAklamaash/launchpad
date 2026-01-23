
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
    invited_user_id: string;
    otp: string;
    infra_id: string;
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
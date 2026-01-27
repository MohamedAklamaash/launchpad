
export interface GithubCallbackInput {
    code: string;
}

export interface GithubUserUpsertInput {
    token: string;
    github_id: string;
    username: string;
    avatar_url: string;
    email: string;
}

import { AuthTokens } from "./auth.invited_user.types";

export interface GithubAuthResponse extends AuthTokens {
    message: string;
    user: {
        id: string;
        email: string;
        user_name: string;
        profile_url: string;
    };
}

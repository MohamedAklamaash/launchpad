export interface GithubCallbackInput {
    code: string;
}

export interface GithubUserUpsertInput {
    token: string;
    github_id: string;
    username: string;
    avatar_url: string;
    email: string | null;
}

import { AuthTokens } from './auth.invited_user.types';

export interface GithubAuthResponse extends AuthTokens {
    message: string;
    user: {
        id: string;
        email: string;
        user_name: string;
        profile_url: string;
    };
}

export interface GithubTokenResponse {
    access_token: string;
    token_type?: string;
    scope?: string;
}

export interface GithubUserResponse {
    login: string;
    id: number;
    avatar_url: string;
    email: string | null;
}

export interface GithubEmail {
    email: string;
    primary: boolean;
    verified: boolean;
    visibility: string | null;
}

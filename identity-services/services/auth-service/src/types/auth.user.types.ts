
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

export interface GithubAuthResponse {
    token: string;
    username: string;
    github_id: number;
    avatar_url: string;
    email: string;
}

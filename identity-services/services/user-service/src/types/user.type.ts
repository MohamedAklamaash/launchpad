export interface User {
    user_id: string;
    user_name: string;
    role: string;
    email: string;
    profile_url?: string;
    created_at: Date;
    updated_at: Date;
    metadata?: Record<string, unknown>;
    infra_id: string[];
    invited_by?: string;
}

export interface CreateUserInput {
    user_id: string;
    email: string;
    user_name: string;
    role: string;
    infra_id: string[];
    profile_url?: string;
    metadata?: Record<string, unknown>;
    invited_by?: string;
}

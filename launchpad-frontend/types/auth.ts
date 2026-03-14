export interface User {
  id: string;
  email: string;
  user_name: string;
  role: 'super_admin' | 'admin' | 'user' | 'guest';
  profile_url?: string;
  infra_id: string[];
  metadata?: {
    github?: {
      id: string;
      username: string;
      token: string;
      profile_url: string;
      email: string;
    };
  };
  created_at: string;
  updated_at: string;
}

export interface AuthResponse {
  access_token: string;
  refresh_token: string;
  user: User;
}

export interface InvitedUser {
  id: string;
  email: string;
  user_name: string;
  role: 'admin' | 'user' | 'guest';
  infra_id: string[];
  invited_by: string;
  created_at: string;
}

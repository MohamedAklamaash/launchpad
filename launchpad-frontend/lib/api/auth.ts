import { apiClient } from './client';
import { AuthResponse, InvitedUser, User } from '@/types/auth';

const AUTH_SERVICE = process.env.NEXT_PUBLIC_AUTH_SERVICE_URL || 'http://localhost:5001';

export const authApi = {
  githubLogin: () => {
    window.location.href = `${AUTH_SERVICE}/api/user/login`;
  },

  getCurrentUser: async (): Promise<User> => {
    const { data } = await apiClient.get('/api/user/me');
    return data;
  },

  logout: async (): Promise<void> => {
    await apiClient.post('/api/user/logout');
    localStorage.removeItem('access_token');
    localStorage.removeItem('refresh_token');
  },

  // Invited user: login with email + password only
  credentialsLogin: async (email: string, password: string): Promise<AuthResponse> => {
    const { data } = await apiClient.post('/api/auth/login', { email, password });
    return data;
  },

  // Verify OTP after first-time registration (GET with query params)
  verifyOtp: async (email: string, otp: string): Promise<AuthResponse> => {
    const { data } = await apiClient.get('/api/auth/authenticate-with-otp', {
      params: { email, otp },
    });
    return data;
  },

  // Forgot password — sends OTP to email
  forgotPassword: async (email: string): Promise<void> => {
    await apiClient.post('/api/auth/forgot-password', { email });
  },

  // Verify reset OTP — returns reset token
  verifyResetOtp: async (email: string, otp: string): Promise<{ token: string }> => {
    const { data } = await apiClient.post('/api/auth/verify-reset-otp', { email, otp });
    return data;
  },

  // Reset password with token from verifyResetOtp
  resetPassword: async (token: string, newPassword: string): Promise<void> => {
    await apiClient.post('/api/auth/reset-password', { token, newPassword });
  },

  // Super admin: invite/register a user to an infra
  inviteUser: async (payload: {
    email: string;
    password: string;
    user_name: string;
    infra_id: string;
    role: 'admin' | 'user' | 'guest';
  }): Promise<{ message: string; user_id: string; otp?: string }> => {
    const { data } = await apiClient.post('/api/auth/register', payload);
    return data;
  },

  // Users the caller has invited, with verification status (is_authenticated)
  listInvitedUsers: async (): Promise<InvitedUser[]> => {
    const { data } = await apiClient.get('/api/auth/invited-users');
    return data;
  },

  // Remove a member from the given orgs; their account is deleted only if it was their last org
  removeMemberFromOrg: async (userId: string, infraIds: string[]): Promise<{ removed: boolean; deleted_account: boolean }> => {
    const { data } = await apiClient.delete(`/api/auth/invited-users/${userId}`, { data: { infra_ids: infraIds } });
    return data;
  },
};

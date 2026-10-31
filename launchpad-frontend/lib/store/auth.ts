'use client';

import { create } from 'zustand';
import { User } from '@/types/auth';
import { authApi } from '@/lib/api/auth';
import { useOrgStore } from '@/lib/store/org';

// A different user can sign in without a page reload, so the previous user's org list must go.
const resetOrgs = () => useOrgStore.setState({ orgs: [], status: 'idle' });

interface AuthState {
  user: User | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  setAuth: (user: User, accessToken: string, refreshToken: string) => void;
  clearAuth: () => void;
  loadUser: () => Promise<void>;
  logout: () => Promise<void>;
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  isAuthenticated: false,
  isLoading: true,

  setAuth: (user, accessToken, refreshToken) => {
    localStorage.setItem('access_token', accessToken);
    localStorage.setItem('refresh_token', refreshToken);
    localStorage.setItem('user', JSON.stringify(user));
    resetOrgs();
    set({ user, isAuthenticated: true, isLoading: false });
  },

  clearAuth: () => {
    localStorage.removeItem('access_token');
    localStorage.removeItem('refresh_token');
    localStorage.removeItem('user');
    resetOrgs();
    set({ user: null, isAuthenticated: false, isLoading: false });
  },

  loadUser: async () => {
    try {
      const token = localStorage.getItem('access_token');
      if (!token) {
        set({ isLoading: false });
        return;
      }

      const cached = localStorage.getItem('user');

      // For invited users (no GitHub profile), skip the /me call and use cache
      if (cached) {
        const parsedUser = JSON.parse(cached);
        // GitHub users have metadata.github; invited users don't
        if (!parsedUser?.metadata?.github) {
          set({ user: parsedUser, isAuthenticated: true, isLoading: false });
          return;
        }
      }

      // GitHub users: validate token via /me
      try {
        const user = await authApi.getCurrentUser();
        set({ user, isAuthenticated: true, isLoading: false });
      } catch {
        if (cached) {
          set({ user: JSON.parse(cached), isAuthenticated: true, isLoading: false });
        } else {
          set({ user: null, isAuthenticated: false, isLoading: false });
          localStorage.removeItem('access_token');
          localStorage.removeItem('refresh_token');
        }
      }
    } catch {
      set({ user: null, isAuthenticated: false, isLoading: false });
      localStorage.removeItem('access_token');
      localStorage.removeItem('refresh_token');
      localStorage.removeItem('user');
    }
  },

  logout: async () => {
    try {
      await authApi.logout();
    } catch (error) {
      console.error('Logout error:', error);
    } finally {
      resetOrgs();
      set({ user: null, isAuthenticated: false });
      localStorage.removeItem('access_token');
      localStorage.removeItem('refresh_token');
      localStorage.removeItem('user');
    }
  },
}));

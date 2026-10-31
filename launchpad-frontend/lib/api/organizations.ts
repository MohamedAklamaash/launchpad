import { apiClient } from './client';
import type { AssignableRole, OrgInvitation, OrgMember, Organization } from '@/types/organization';

export interface SwitchOrgResult {
  accessToken?: string;
  refreshToken?: string;
  access_token?: string;
  refresh_token?: string;
}

export interface AcceptInvitationResult {
  organization_id: string;
  role: string;
}

export const organizationApi = {
  list: async (): Promise<Organization[]> => {
    const { data } = await apiClient.get('/api/organizations');
    return data;
  },

  create: async (name: string): Promise<Organization> => {
    const { data } = await apiClient.post('/api/organizations', { name });
    return data;
  },

  rename: async (id: string, name: string): Promise<{ id: string; name: string }> => {
    const { data } = await apiClient.patch(`/api/organizations/${id}`, { name });
    return data;
  },

  members: async (id: string): Promise<OrgMember[]> => {
    const { data } = await apiClient.get(`/api/organizations/${id}/members`);
    return data;
  },

  changeRole: async (id: string, accountId: string, role: AssignableRole): Promise<void> => {
    await apiClient.patch(`/api/organizations/${id}/members/${accountId}`, { role });
  },

  // Also how a member leaves: pass their own account id.
  removeMember: async (id: string, accountId: string): Promise<void> => {
    await apiClient.delete(`/api/organizations/${id}/members/${accountId}`);
  },

  invitations: async (id: string): Promise<OrgInvitation[]> => {
    const { data } = await apiClient.get(`/api/organizations/${id}/invitations`);
    return data;
  },

  invite: async (id: string, email: string, role: AssignableRole): Promise<void> => {
    await apiClient.post(`/api/organizations/${id}/invitations`, { email, role });
  },

  revokeInvitation: async (id: string, invitationId: string): Promise<void> => {
    await apiClient.delete(`/api/organizations/${id}/invitations/${invitationId}`);
  },

  // 401 with code reauth_required is handled by the response interceptor in client.ts.
  transferOwnership: async (id: string, newOwnerAccountId: string): Promise<void> => {
    await apiClient.post(`/api/organizations/${id}/transfer-ownership`, {
      new_owner_account_id: newOwnerAccountId,
    });
  },

  switchOrg: async (organizationId: string, refreshToken: string): Promise<SwitchOrgResult> => {
    const { data } = await apiClient.post('/api/auth/switch-org', {
      organization_id: organizationId,
      refresh_token: refreshToken,
    });
    return data;
  },

  // The invitation token goes in the body only. The bearer header is added by the client's
  // request interceptor; nothing about the token touches the URL.
  acceptInvitation: async (token: string): Promise<AcceptInvitationResult> => {
    const { data } = await apiClient.post('/api/invitations/accept', { token });
    return data;
  },
};

// Stores the tokens a switch-org call returned, the same way login and the 401 refresh
// path do. Callers then reload at the dashboard: pages hold per-org data in local state
// and a resource from the previous org would 404 in the new one.
export async function switchToOrg(organizationId: string): Promise<void> {
  const refresh = localStorage.getItem('refresh_token');
  if (!refresh) throw new Error('No refresh token');
  const res = await organizationApi.switchOrg(organizationId, refresh);
  const access = res.accessToken || res.access_token;
  const nextRefresh = res.refreshToken || res.refresh_token;
  if (!access || !nextRefresh) throw new Error('Switch failed');
  localStorage.setItem('access_token', access);
  localStorage.setItem('refresh_token', nextRefresh);
}

export function goToDashboard(): void {
  window.location.assign(`${process.env.NEXT_PUBLIC_BASE_PATH || ''}/dashboard`);
}

// Backend messages are already written for people (409, 429, 503), so show them as-is and
// only fall back to a line by status when there is none. Gateway validation errors come
// back as `detail` and are not user-friendly, so they get the generic 422 line.
export function apiErrorMessage(err: unknown, fallback: string): string {
  const res = (err as { response?: { status?: number; data?: { error?: unknown; message?: unknown } } })?.response;
  for (const v of [res?.data?.error, res?.data?.message]) {
    if (typeof v === 'string' && v) return v;
  }
  switch (res?.status) {
    case 403:
      return 'Your role does not allow this.';
    case 404:
      return 'Not found in this organization.';
    case 429:
      return 'Too many requests. Wait a little and try again.';
    case 503:
      return 'Temporarily unavailable. Try again shortly.';
    case 422:
      return 'That value is not valid.';
  }
  return fallback;
}

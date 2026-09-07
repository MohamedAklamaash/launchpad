import { apiClient } from './client';
import { ComputeType, Infrastructure, InfrastructureCreate, InfrastructureCreateResponse } from '@/types/infrastructure';

export interface AwsRegion {
  value: string;
  label: string;
}

export interface ComputeCapability {
  value: ComputeType;
  label: string;
  enabled: boolean;
}

export interface PlatformCapabilities {
  compute_types: ComputeCapability[];
}

export const infrastructureApi = {
  list: async (): Promise<Infrastructure[]> => {
    const { data } = await apiClient.get('/api/infrastructures/');
    return data;
  },

  get: async (id: string): Promise<Infrastructure> => {
    const { data } = await apiClient.get(`/api/infrastructures/${id}/`);
    return data;
  },

  create: async (payload: InfrastructureCreate): Promise<InfrastructureCreateResponse> => {
    const { data } = await apiClient.post('/api/infrastructures/', payload);
    return data;
  },

  delete: async (id: string): Promise<void> => {
    await apiClient.delete(`/api/infrastructures/${id}/`);
  },

  removeUser: async (infraId: string, userId: string): Promise<void> => {
    await apiClient.delete(`/api/infrastructures/${infraId}/users/${userId}/`);
  },

  updateConfig: async (id: string, payload: { name?: string; max_cpu?: number; max_memory?: number }): Promise<Infrastructure> => {
    const { data } = await apiClient.patch(`/api/infrastructures/${id}/update/`, payload);
    return data;
  },

  reprovision: async (id: string): Promise<void> => {
    await apiClient.post(`/api/infrastructures/${id}/reprovision/`);
  },

  validate: async (id: string): Promise<{ can_delete: boolean; app_count: number }> => {
    const { data } = await apiClient.get(`/api/infrastructures/${id}/validation/`);
    return data;
  },

  listRegions: async (): Promise<AwsRegion[]> => {
    const { data } = await apiClient.get('/api/aws/regions');
    return data;
  },

  // Which compute targets this deployment will actually accept. EKS_ENABLED is a
  // server-side flag, so the dashboard has to ask rather than assume.
  listCapabilities: async (): Promise<PlatformCapabilities> => {
    const { data } = await apiClient.get('/api/infrastructures/capabilities');
    return data;
  },

  // Plaintext is returned exactly once; issuing again revokes prior keys.
  issueScriptApiKey: async (): Promise<{ api_key: string }> => {
    const { data } = await apiClient.post('/api/infrastructures/script-api-key');
    return data;
  },
};

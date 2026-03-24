import { apiClient } from './client';
import { Infrastructure, InfrastructureCreate } from '@/types/infrastructure';

export interface AwsRegion {
  value: string;
  label: string;
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

  create: async (payload: InfrastructureCreate): Promise<Infrastructure> => {
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
};

import { apiClient } from './client';
import { Application, ApplicationCreate, ApplicationUpdate } from '@/types/application';

export const applicationApi = {
  list: async (infrastructureId: string): Promise<Application[]> => {
    const { data } = await apiClient.get(`/api/applications/?infrastructure_id=${infrastructureId}`);
    return data;
  },

  get: async (id: string): Promise<Application> => {
    const { data } = await apiClient.get(`/api/applications/${id}/`);
    return data;
  },

  create: async (payload: ApplicationCreate): Promise<Application> => {
    const { data } = await apiClient.post('/api/applications/', payload);
    return data;
  },

  update: async (id: string, payload: ApplicationUpdate): Promise<Application> => {
    const { data } = await apiClient.patch(`/api/applications/${id}/update/`, payload);
    return data;
  },

  delete: async (id: string): Promise<void> => {
    await apiClient.delete(`/api/applications/${id}/`);
  },

  deploy: async (id: string): Promise<void> => {
    await apiClient.post(`/api/applications/${id}/deploy/`);
  },

  sleep: async (id: string): Promise<void> => {
    await apiClient.post(`/api/applications/${id}/sleep/`);
  },

  wake: async (id: string): Promise<void> => {
    await apiClient.post(`/api/applications/${id}/wake/`);
  },
};

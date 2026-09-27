import { apiClient } from './client';
import { Application, ApplicationCreate, ApplicationUpdate, Deployment, RollbackPreview } from '@/types/application';

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

  rotateWebhookSecret: async (id: string): Promise<{ webhook_url: string; secret: string; instructions: string }> => {
    const { data } = await apiClient.post(`/api/applications/${id}/webhook-secret`);
    return data;
  },

  listDeployments: async (id: string): Promise<Deployment[]> => {
    const { data } = await apiClient.get(`/api/applications/${id}/deployments`);
    return data;
  },

  getRollbackPreview: async (id: string, deploymentId: string): Promise<RollbackPreview> => {
    const { data } = await apiClient.get(`/api/applications/${id}/deployments/${deploymentId}/preview`);
    return data;
  },

  rollback: async (id: string, deploymentId: string): Promise<void> => {
    await apiClient.post(`/api/applications/${id}/deployments/${deploymentId}/rollback`);
  },

  resumeAutoDeploy: async (id: string): Promise<void> => {
    await apiClient.post(`/api/applications/${id}/resume-auto-deploy`);
  },
};

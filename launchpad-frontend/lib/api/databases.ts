import { apiClient } from './client';
import { DatabaseCreate, ManagedDatabase } from '@/types/database';

export const databaseApi = {
  list: async (infraId: string): Promise<ManagedDatabase[]> => {
    const { data } = await apiClient.get(`/api/infrastructures/${infraId}/databases/`);
    return data;
  },

  get: async (infraId: string, databaseId: string): Promise<ManagedDatabase> => {
    const { data } = await apiClient.get(`/api/infrastructures/${infraId}/databases/${databaseId}`);
    return data;
  },

  create: async (infraId: string, payload: DatabaseCreate): Promise<ManagedDatabase> => {
    const { data } = await apiClient.post(`/api/infrastructures/${infraId}/databases/`, payload);
    return data;
  },

  // Typed-name confirmation enforced server-side too — confirmName must equal the database's name.
  delete: async (infraId: string, databaseId: string, confirmName: string): Promise<ManagedDatabase> => {
    const { data } = await apiClient.delete(`/api/infrastructures/${infraId}/databases/${databaseId}`, {
      data: { confirm_name: confirmName },
    });
    return data;
  },
};

import { apiClient } from './client';
import { ComputeType, Infrastructure, InfrastructureCosts, InfrastructureCreate, InfrastructureCreateResponse, ProvisioningLogs } from '@/types/infrastructure';

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

  getLogs: async (id: string): Promise<ProvisioningLogs> => {
    const { data } = await apiClient.get(`/api/infrastructures/${id}/logs`);
    return data;
  },

  // Owner only. Zip: rendered IAM policy, expected trust-policy shape, a live drift
  // check against the customer's AWS account, and an honest limitations section.
  downloadEvidencePack: async (id: string): Promise<Blob> => {
    const { data } = await apiClient.get(`/api/infrastructures/${id}/evidence-pack`, {
      responseType: 'blob',
    });
    return data;
  },

  // Owner only. Zip: README + revocation instructions, a regenerated Terraform bundle,
  // redacted task-definitions/Kubernetes manifests, the CI buildspec, and a GitHub
  // webhook removal list. Requires a JWT issued within the last few minutes — a 401 with
  // code "reauth_required" is handled by the axios interceptor (forces re-login).
  downloadExitExport: async (id: string): Promise<Blob> => {
    const { data } = await apiClient.get(`/api/infrastructures/${id}/exit-export`, {
      responseType: 'blob',
    });
    return data;
  },

  // Owner only. Requests platform DNS teardown for this infrastructure and marks it
  // exited. The one exit action that changes anything; the export itself is read-only.
  // 202 (dns_teardown: "pending") is a distinct, non-final outcome from 200 (confirmed) —
  // callers need the status code, not just the body, to tell them apart.
  completeExit: async (id: string): Promise<{ status: number; data: { status: string; dns_teardown: string; message?: string } }> => {
    const response = await apiClient.post(`/api/infrastructures/${id}/exit`, { confirm: true });
    return { status: response.status, data: response.data };
  },

  getCosts: async (id: string, months: number = 1): Promise<InfrastructureCosts> => {
    const { data } = await apiClient.get(`/api/infrastructures/${id}/costs`, { params: { months } });
    return data;
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

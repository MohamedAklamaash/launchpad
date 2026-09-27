import { apiClient } from './client';
import { CustomDomain } from '@/types/custom-domain';

export const customDomainApi = {
  list: async (infraId: string): Promise<CustomDomain[]> => {
    const { data } = await apiClient.get(`/api/infrastructures/${infraId}/custom-domains/`);
    return data;
  },

  // Requests a per-domain ACM certificate and returns the DNS records to publish in the
  // customer's own DNS. The ownership TXT value is shown only in this response — save it
  // now, it is never returned again.
  claim: async (infraId: string, applicationId: string, hostname: string): Promise<CustomDomain> => {
    const { data } = await apiClient.post(`/api/infrastructures/${infraId}/custom-domains/`, {
      application_id: applicationId,
      hostname,
    });
    return data;
  },

  // A 409 here means the ALB's SNI certificate cap is reached or another account just
  // validated the same hostname first.
  verify: async (infraId: string, domainId: string): Promise<CustomDomain> => {
    const { data } = await apiClient.post(`/api/infrastructures/${infraId}/custom-domains/${domainId}/verify`);
    return data;
  },

  // A PENDING claim is removed outright; a VALIDATED domain is detached from the ALB, has
  // its certificate deleted, and is marked DISABLED.
  remove: async (infraId: string, domainId: string): Promise<void> => {
    await apiClient.delete(`/api/infrastructures/${infraId}/custom-domains/${domainId}`);
  },
};

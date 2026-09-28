export type CustomDomainStatus = 'PENDING' | 'VALIDATED' | 'DISABLED';

export interface DnsRecord {
  name: string;
  type: string;
  value: string | null;
}

export interface CustomDomain {
  id: string;
  application_id: string | null;
  hostname: string;
  status: CustomDomainStatus;
  expires_at: string;
  last_verified_at: string | null;
  created_at: string;
  /** `ownership_txt.value` is the plaintext ownership token — present only in the claim
   * response, never again afterward (see api/models/custom_domain.py:issue_ownership_token). */
  ownership_txt: DnsRecord;
  cname_to_edge: DnsRecord;
  acm_validation: DnsRecord | null;
}

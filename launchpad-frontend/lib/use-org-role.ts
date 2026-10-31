'use client';

import { useEffect } from 'react';
import { ORGS_ENABLED } from '@/lib/orgs-enabled';
import { useOrgStore } from '@/lib/store/org';
import { currentSessionClaims } from '@/lib/session-claims';
import { Capability, roleCan } from '@/lib/org-matrix';
import type { Organization, OrgRole } from '@/types/organization';

export interface OrgRoleState {
  enabled: boolean;
  loading: boolean;
  org: Organization | null;
  role: OrgRole | null;
  // Pass the pre-F9 check as `legacy`. With the flag off the legacy answer is returned
  // untouched; with it on, only the matrix decides, and an unknown role (still loading,
  // unverified credential, empty list) answers no.
  allow: (capability: Capability, legacy: boolean) => boolean;
}

export const NO_PERMISSION_HINT = 'Your role in this organization does not allow this.';

export function pickActiveOrg(orgs: Organization[], orgId: string | undefined): Organization | null {
  if (orgId) return orgs.find((o) => o.id === orgId) ?? null;
  // A token without org_id means the caller's personal org.
  return orgs.find((o) => o.is_personal && o.role === 'owner') ?? null;
}

export function useOrgRole(): OrgRoleState {
  const orgs = useOrgStore((s) => s.orgs);
  const status = useOrgStore((s) => s.status);
  const load = useOrgStore((s) => s.load);

  useEffect(() => {
    if (ORGS_ENABLED) void load();
  }, [load]);

  if (!ORGS_ENABLED) {
    return { enabled: false, loading: false, org: null, role: null, allow: (_c, legacy) => legacy };
  }

  const org = status === 'ready' ? pickActiveOrg(orgs, currentSessionClaims().orgId) : null;
  const role = org?.role ?? null;
  return {
    enabled: true,
    loading: status === 'idle' || status === 'loading',
    org,
    role,
    allow: (capability) => (role ? roleCan(role, capability) : false),
  };
}

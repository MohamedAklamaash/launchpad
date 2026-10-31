'use client';

import { create } from 'zustand';
import { organizationApi } from '@/lib/api/organizations';
import type { Organization } from '@/types/organization';

interface OrgState {
  orgs: Organization[];
  status: 'idle' | 'loading' | 'ready' | 'error';
  load: (force?: boolean) => Promise<void>;
}

// The org list is fetched once per page load and shared by the switcher and every
// role-gated page. Callers pass force after a create or rename.
export const useOrgStore = create<OrgState>((set, get) => ({
  orgs: [],
  status: 'idle',

  load: async (force = false) => {
    const { status } = get();
    if (status === 'loading' || (status === 'ready' && !force)) return;
    set({ status: 'loading' });
    try {
      const orgs = await organizationApi.list();
      set({ orgs, status: 'ready' });
    } catch {
      set({ orgs: [], status: 'error' });
    }
  },
}));

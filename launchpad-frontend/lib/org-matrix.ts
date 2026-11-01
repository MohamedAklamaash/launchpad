import type { OrgRole } from '@/types/organization';

// Mirror of MATRIX in deployment-services/shared/authz/org_matrix.py. The backend enforces
// it; this only decides what the UI shows. Keep the two in step.
export type Capability =
  | 'create_infrastructure'
  | 'delete_infrastructure'
  | 'deploy'
  | 'rollback'
  | 'logs_metrics'
  | 'costs'
  | 'env_vars'
  | 'databases'
  | 'custom_domains'
  | 'exit'
  | 'members_invite'
  | 'members_remove'
  | 'members_change_role'
  | 'billing'
  | 'delete_application'
  | 'manage_infrastructure';

const OWNER: OrgRole[] = ['owner'];
const OWNER_ADMIN: OrgRole[] = ['owner', 'admin'];
const OWNER_ADMIN_DEV: OrgRole[] = ['owner', 'admin', 'developer'];

const MATRIX: Record<Capability, OrgRole[]> = {
  create_infrastructure: OWNER_ADMIN,
  delete_infrastructure: OWNER,
  deploy: OWNER_ADMIN_DEV,
  rollback: OWNER_ADMIN_DEV,
  logs_metrics: OWNER_ADMIN_DEV,
  costs: OWNER_ADMIN,
  env_vars: OWNER_ADMIN_DEV,
  databases: OWNER_ADMIN,
  custom_domains: OWNER_ADMIN,
  exit: OWNER,
  members_invite: OWNER_ADMIN,
  members_remove: OWNER_ADMIN,
  members_change_role: OWNER_ADMIN,
  billing: OWNER,
  delete_application: OWNER_ADMIN,
  manage_infrastructure: OWNER,
};

export function roleCan(role: OrgRole, capability: Capability): boolean {
  return MATRIX[capability].includes(role);
}

export const ROLE_LABEL: Record<OrgRole, string> = {
  owner: 'Owner',
  admin: 'Admin',
  developer: 'Developer',
  viewer: 'Viewer',
};

export const ASSIGNABLE_ROLES = ['admin', 'developer', 'viewer'] as const;

export function assignableRolesFor(actor: OrgRole): readonly (typeof ASSIGNABLE_ROLES)[number][] {
  return actor === 'owner' ? ASSIGNABLE_ROLES : ASSIGNABLE_ROLES.filter((r) => r !== 'admin');
}

export function canManageMember(actor: OrgRole, target: OrgRole): boolean {
  if (!roleCan(actor, 'members_remove') || target === 'owner') return false;
  return target !== 'admin' || actor === 'owner';
}

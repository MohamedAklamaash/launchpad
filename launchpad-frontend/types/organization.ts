export type OrgRole = 'owner' | 'admin' | 'developer' | 'viewer';
export type AssignableRole = Exclude<OrgRole, 'owner'>;

export interface Organization {
  id: string;
  name: string;
  role: OrgRole;
  is_personal: boolean;
}

// A viewer's list has no account_id or email; managers get both.
export interface OrgMember {
  id: string;
  account_id?: string;
  display_name: string;
  email?: string;
  role: OrgRole;
  joined_at: string;
}

export interface OrgInvitation {
  id: string;
  email: string;
  role: AssignableRole;
  invited_by_account_id: string;
  expires_at: string;
  created_at: string;
}

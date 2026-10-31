import { HttpError } from '@launchpad/common';
import { Membership } from '@/db';
import { ORG_ROLE, type OrgRole } from '@/types/org-role';
import type { AccessTokenPayload } from '@/utils/handle-token';
import { assertOrgsEnabled } from './org-guard';
import { findCredentialRow } from './switch-org.service';

const MANAGER_ROLES: readonly OrgRole[] = [ORG_ROLE.OWNER, ORG_ROLE.ADMIN];

export interface OrgManager {
    accountId: string;
    role: OrgRole;
    userName: string;
}

const orgNotFound = () => new HttpError(404, 'Organization not found');

// Resolves "who is asking" for owner/admin-only org routes. The token's `account_id` claim
// is a lookup key, never the authorization itself: the credential row must still be
// verified and still linked to that exact account, and an ACTIVE (non-tombstoned)
// Membership must exist in the path's org with a manager role. A caller who is not a
// member gets the same 404 as an org that does not exist, so org ids cannot be probed;
// only a real member whose role is too low gets a 403.
export const requireOrgManager = async (
    payload: AccessTokenPayload,
    organizationId: string,
): Promise<OrgManager> => {
    assertOrgsEnabled();

    const claimedAccountId = payload.account_id;
    if (!claimedAccountId) throw orgNotFound();

    const credential = await findCredentialRow(payload.sub);
    if (
        !credential ||
        credential.email_verified_at === null ||
        credential.account_id !== claimedAccountId
    ) {
        throw orgNotFound();
    }

    const membership = await Membership.findOne({
        where: { organization_id: organizationId, account_id: claimedAccountId, removed_at: null },
    });
    if (!membership) throw orgNotFound();
    if (!MANAGER_ROLES.includes(membership.role)) {
        throw new HttpError(403, 'Only an owner or admin can manage invitations');
    }

    return { accountId: claimedAccountId, role: membership.role, userName: credential.user_name };
};

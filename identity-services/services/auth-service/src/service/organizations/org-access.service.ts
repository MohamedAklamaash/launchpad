import { HttpError } from '@launchpad/common';
import { Membership } from '@/db';
import { ORG_ROLE, type OrgRole } from '@/types/org-role';
import type { AccessTokenPayload } from '@/utils/handle-token';
import { assertOrgsEnabled } from './org-guard';
import { MANAGER_ROLES } from './actor-lock';
import { findCredentialRow } from './switch-org.service';

export interface VerifiedCaller {
    accountId: string;
    userName: string;
}

export interface ActiveMember extends VerifiedCaller {
    role: OrgRole;
}

export type OrgManager = ActiveMember;

const orgNotFound = () => new HttpError(404, 'Organization not found');

// The token's `account_id` claim is a lookup key, never the authorization itself: the
// credential row must still be verified and still linked to that exact account. Null means
// "not a verified caller"; each route decides how to word that.
export const resolveVerifiedCaller = async (
    payload: AccessTokenPayload,
): Promise<VerifiedCaller | null> => {
    assertOrgsEnabled();

    const claimedAccountId = payload.account_id;
    if (!claimedAccountId) return null;

    const credential = await findCredentialRow(payload.sub);
    if (
        !credential ||
        credential.email_verified_at === null ||
        credential.account_id !== claimedAccountId
    ) {
        return null;
    }
    return { accountId: claimedAccountId, userName: credential.user_name };
};

export const requireVerifiedCaller = async (
    payload: AccessTokenPayload,
): Promise<VerifiedCaller> => {
    const caller = await resolveVerifiedCaller(payload);
    if (!caller) throw new HttpError(403, 'A verified account is required');
    return caller;
};

// Resolves "who is asking" for org routes. On top of a verified caller, an ACTIVE
// (non-tombstoned) Membership must exist in the path's org. A caller who is not a member
// gets the same 404 as an org that does not exist, so org ids cannot be probed.
export const requireActiveMember = async (
    payload: AccessTokenPayload,
    organizationId: string,
): Promise<ActiveMember> => {
    const caller = await resolveVerifiedCaller(payload);
    if (!caller) throw orgNotFound();

    const membership = await Membership.findOne({
        where: { organization_id: organizationId, account_id: caller.accountId, removed_at: null },
    });
    if (!membership) throw orgNotFound();

    return { ...caller, role: membership.role };
};

export const assertManager = (member: ActiveMember): void => {
    if (!MANAGER_ROLES.includes(member.role)) {
        throw new HttpError(403, 'Only an owner or admin can do this');
    }
};

export const requireOrgManager = async (
    payload: AccessTokenPayload,
    organizationId: string,
): Promise<OrgManager> => {
    const member = await requireActiveMember(payload, organizationId);
    assertManager(member);
    return member;
};

export const requireOrgOwner = async (
    payload: AccessTokenPayload,
    organizationId: string,
): Promise<OrgManager> => {
    const member = await requireActiveMember(payload, organizationId);
    if (member.role !== ORG_ROLE.OWNER) {
        throw new HttpError(403, 'Only the organization owner can do this');
    }
    return member;
};

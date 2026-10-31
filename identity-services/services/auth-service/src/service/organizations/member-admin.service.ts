import { HttpError, sanitizeDisplayName } from '@launchpad/common';
import { Op } from 'sequelize';
import { Account, Membership } from '@/db';
import { ORG_ROLE, type OrgRole } from '@/types/org-role';
import type { AccessTokenPayload } from '@/utils/handle-token';
import { assertManager, requireActiveMember, requireOrgManager } from './org-access.service';
import { changeMemberRole, removeMember } from './membership.service';
import { enforceOrgActionSlot } from './org-rate-limit';

// `id` is the membership row id: opaque, random, and not derivable from anyone's email.
// `account_id` is uuid5(email) under a public namespace, so it confirms a guessed email
// offline; it is withheld from viewers, who also get no email. Managers need it for the
// change-role and remove routes, which address a member by account id.
export interface MemberSummary {
    id: string;
    account_id?: string;
    display_name: string;
    email?: string;
    role: OrgRole;
    joined_at: Date;
}

const ANONYMOUS_NAME = 'Member';

const canSeeEmails = (role: OrgRole): boolean => role !== ORG_ROLE.VIEWER;

// Names are user-typed text (often an email). Everyone gets the sanitized form; a viewer,
// who must not learn addresses, gets a neutral name for anything that looked like one.
const displayNameFor = (raw: string | null | undefined, viewerRole: OrgRole): string => {
    if (viewerRole === ORG_ROLE.VIEWER && raw?.includes('@')) return ANONYMOUS_NAME;
    return sanitizeDisplayName(raw ?? '') || ANONYMOUS_NAME;
};

export const listMembers = async (
    caller: AccessTokenPayload,
    organizationId: string,
): Promise<MemberSummary[]> => {
    const viewer = await requireActiveMember(caller, organizationId);
    const memberships = await Membership.findAll({
        where: { organization_id: organizationId, removed_at: null },
        order: [['created_at', 'ASC']],
    });
    const accounts = await Account.findAll({
        where: { id: { [Op.in]: memberships.map((m) => m.account_id) } },
        attributes: ['id', 'email', 'display_name'],
    });
    const byId = new Map(accounts.map((account) => [account.id, account]));
    const withEmail = canSeeEmails(viewer.role);

    return memberships.map((membership) => {
        const account = byId.get(membership.account_id);
        return {
            id: membership.id,
            ...(withEmail ? { account_id: membership.account_id } : {}),
            display_name: displayNameFor(account?.display_name, viewer.role),
            ...(withEmail && account ? { email: account.email } : {}),
            role: membership.role,
            joined_at: membership.created_at,
        };
    });
};

export interface ChangeRoleResult {
    organization_id: string;
    account_id: string;
    role: OrgRole;
    version: number;
}

// Matrix row "change role": owner and admin only. Owner is never a source or a target role
// here: changeMemberRole rejects an owner row and the owner role, and the route schema
// refuses `owner` before this runs. Ownership moves only through transfer. The actor is
// re-verified as a manager under row locks inside the transaction.
export const changeOrgMemberRole = async (
    caller: AccessTokenPayload,
    organizationId: string,
    targetAccountId: string,
    newRole: OrgRole,
): Promise<ChangeRoleResult> => {
    const actor = await requireOrgManager(caller, organizationId);
    await enforceOrgActionSlot(organizationId, actor.accountId);
    const membership = await changeMemberRole({
        organizationId,
        accountId: targetAccountId,
        newRole,
        actorAccountId: actor.accountId,
        lockActorAsManager: true,
    });
    return {
        organization_id: membership.organization_id,
        account_id: membership.account_id,
        role: membership.role,
        version: membership.version,
    };
};

// Matrix row "remove": owner and admin (never the owner). Any active member may remove
// themselves (leave), except the owner, who must transfer first. "Self" is the verified
// credential's account, compared to the path id, never the raw token claim.
export const removeOrLeaveOrganization = async (
    caller: AccessTokenPayload,
    organizationId: string,
    targetAccountId: string,
): Promise<void> => {
    const actor = await requireActiveMember(caller, organizationId);
    const isSelf = actor.accountId === targetAccountId;

    if (isSelf && actor.role === ORG_ROLE.OWNER) {
        throw new HttpError(409, 'The owner cannot leave; transfer ownership first');
    }
    if (!isSelf) {
        assertManager(actor);
        await enforceOrgActionSlot(organizationId, actor.accountId);
    }

    await removeMember({
        organizationId,
        accountId: targetAccountId,
        actorAccountId: actor.accountId,
        lockActorAsManager: !isSelf,
    });
};

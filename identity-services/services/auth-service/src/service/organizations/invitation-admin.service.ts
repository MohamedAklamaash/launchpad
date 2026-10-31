import { HttpError, ORG_INVITATION_EMAIL_EVENT } from '@launchpad/common';
import { Op } from 'sequelize';
import { Invitation, Membership, Organization } from '@/db';
import { userAuthenticationQueue } from '@/messaging/producer/user-created.message';
import { ORG_ROLE, type OrgRole } from '@/types/org-role';
import { accountIdForEmail } from '@/utils/account-id';
import type { AccessTokenPayload } from '@/utils/handle-token';
import { logger } from '@/utils/logger';
import { otpAttempts } from '@/utils/otp-attempts';
import { issueInvitation, revokeInvitation } from './invitation.service';
import { requireOrgManager } from './org-access.service';

const INVITABLE_ROLES: readonly OrgRole[] = [ORG_ROLE.ADMIN, ORG_ROLE.DEVELOPER, ORG_ROLE.VIEWER];

export interface InvitationSummary {
    id: string;
    email: string;
    role: OrgRole;
    invited_by_account_id: string;
    expires_at: Date;
    created_at: Date;
}

const toSummary = (invitation: Invitation): InvitationSummary => ({
    id: invitation.id,
    email: invitation.email,
    role: invitation.role,
    invited_by_account_id: invitation.invited_by_account_id,
    expires_at: invitation.expires_at,
    created_at: invitation.created_at,
});

// Fail closed: an unreachable Redis is not a reason to lift an abuse limit, so a counter
// error refuses the invite (503) instead of letting it through.
const enforce = async (consume: () => Promise<boolean>, tooMany: string): Promise<void> => {
    let allowed: boolean;
    try {
        allowed = await consume();
    } catch (error) {
        logger.error({ err: error }, 'invite rate limit check failed, refusing invite');
        throw new HttpError(503, 'Invites are temporarily unavailable - try again shortly');
    }
    if (!allowed) throw new HttpError(429, tooMany);
};

export interface CreateInvitationInput {
    caller: AccessTokenPayload;
    organizationId: string;
    email: string;
    role: OrgRole;
}

// Order matters: authorize first so a non-member can never spend an org's invite budget,
// then the two rate limits, then mint. The plaintext token exists only in this function's
// scope and in the queued email job; it is never logged and never returned.
export const createInvitation = async (
    input: CreateInvitationInput,
): Promise<InvitationSummary> => {
    const manager = await requireOrgManager(input.caller, input.organizationId);
    if (!INVITABLE_ROLES.includes(input.role)) {
        throw new HttpError(409, 'Cannot invite a member with this role');
    }

    const alreadyMember = await Membership.findOne({
        where: {
            organization_id: input.organizationId,
            account_id: accountIdForEmail(input.email),
            removed_at: null,
        },
    });
    if (alreadyMember) throw new HttpError(409, 'This email already belongs to a member');

    await enforce(
        () => otpAttempts.consumeOrgEmailInviteSlot(input.organizationId, input.email),
        'Too many invites for this email in this organization - try again later',
    );
    await enforce(
        () => otpAttempts.consumeGlobalEmailInviteSlot(input.email),
        'This address cannot receive more invitations right now - try again later',
    );
    await enforce(
        () => otpAttempts.consumeOrgInviteSlot(input.organizationId),
        'Too many invitations for this organization - try again later',
    );

    const organization = await Organization.findByPk(input.organizationId);
    if (!organization) throw new HttpError(404, 'Organization not found');

    const { invitation, token } = await issueInvitation({
        organizationId: input.organizationId,
        email: input.email,
        role: input.role,
        invitedByAccountId: manager.accountId,
    });

    try {
        await userAuthenticationQueue.add(
            ORG_INVITATION_EMAIL_EVENT,
            {
                email: invitation.email,
                token,
                organization_name: organization.name,
                inviter_name: manager.userName,
                role: invitation.role,
                expires_at: invitation.expires_at.toISOString(),
            },
            { removeOnComplete: true, removeOnFail: { age: 3600 } },
        );
    } catch (error) {
        logger.error(
            { err: error, invitation_id: invitation.id },
            'failed to enqueue invitation email',
        );
        throw new HttpError(503, 'Invitation email could not be queued - revoke and re-invite');
    }

    return toSummary(invitation);
};

export const listPendingInvitations = async (
    caller: AccessTokenPayload,
    organizationId: string,
): Promise<InvitationSummary[]> => {
    await requireOrgManager(caller, organizationId);
    const pending = await Invitation.findAll({
        where: {
            organization_id: organizationId,
            accepted_at: null,
            revoked_at: null,
            expires_at: { [Op.gt]: new Date() },
        },
        order: [['created_at', 'DESC']],
    });
    return pending.map(toSummary);
};

export const revokeOrgInvitation = async (
    caller: AccessTokenPayload,
    organizationId: string,
    invitationId: string,
): Promise<void> => {
    const manager = await requireOrgManager(caller, organizationId);
    await revokeInvitation(invitationId, organizationId, manager.accountId);
};

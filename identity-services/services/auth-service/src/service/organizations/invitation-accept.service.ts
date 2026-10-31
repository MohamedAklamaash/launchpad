import { HttpError } from '@launchpad/common';
import { UniqueConstraintError, type Transaction } from 'sequelize';
import { Invitation, Membership, Organization } from '@/db';
import { sequelize } from '@/db/sequalize';
import { ORG_ROLE } from '@/types/org-role';
import { accountIdForEmail, normalizeEmail } from '@/utils/account-id';
import { logger } from '@/utils/logger';
import { linkAccountForVerifiedIdentity } from '@/service/account-link.service';
import { recordAuditEvent } from './audit-log.service';
import { verifyInvitationToken } from './invitation.service';
import { addMemberInTransaction } from './membership.service';
import { enqueueInvitationAccepted } from './org-event-outbox.service';
import { assertOrgsEnabled } from './org-guard';
import { findCredentialRow, type CredentialRow } from './switch-org.service';

export interface AcceptInvitationInput {
    callerId: string;
    token: string;
}

export interface AcceptInvitationResult {
    organization_id: string;
    role: string;
}

// One message for every reason the token cannot be redeemed by this caller (unknown,
// expired, used, revoked, inviter no longer allowed to invite, unverified credential,
// email mismatch). A holder of a leaked token learns nothing about which check failed and
// cannot use the response to read the invited address.
const invalidInvitation = () =>
    new HttpError(400, 'Invitation is invalid, expired, or was issued to a different account');

const MANAGER_ROLES: readonly string[] = [ORG_ROLE.OWNER, ORG_ROLE.ADMIN];

const inviterCanStillInvite = async (
    organizationId: string,
    inviterAccountId: string,
    transaction: Transaction,
): Promise<boolean> => {
    const inviter = await Membership.findOne({
        where: { organization_id: organizationId, account_id: inviterAccountId, removed_at: null },
        transaction,
    });
    return inviter !== null && MANAGER_ROLES.includes(inviter.role);
};

// Proof of control of the invited address: the caller is an authenticated session whose
// credential row is email-verified (an InvitedUser completed OTP; a GitHub User's email is
// the primary verified one) and whose normalized email equals the invitation's. The email
// is read from the credential row, never from the token's `email` claim or the request.
const requireVerifiedMatchingCredential = (
    credential: CredentialRow | null,
    invitationEmail: string,
): CredentialRow => {
    if (!credential || credential.email_verified_at === null) throw invalidInvitation();
    if (normalizeEmail(credential.email) !== invitationEmail) throw invalidInvitation();
    return credential;
};

// Defense in depth for removal: removeMember revokes pending invitations, but an
// invitation issued before the member's tombstone can never revive that membership.
const rejectIfIssuedBeforeRemoval = async (
    invitation: Invitation,
    accountId: string,
    transaction: Transaction,
): Promise<void> => {
    const prior = await Membership.findOne({
        where: { organization_id: invitation.organization_id, account_id: accountId },
        transaction,
    });
    if (prior?.removed_at && invitation.created_at.getTime() <= prior.removed_at.getTime()) {
        throw invalidInvitation();
    }
};

const resolveAccountId = async (
    credential: CredentialRow,
    invitationEmail: string,
    transaction: Transaction,
): Promise<string> => {
    const expected = accountIdForEmail(invitationEmail);
    const linked =
        credential.account_id ??
        (await linkAccountForVerifiedIdentity({
            email: credential.email,
            displayName: credential.user_name,
            table: credential.table,
            rowId: credential.id,
            transaction,
        }));
    if (linked !== expected) {
        logger.warn(
            { credential_id: credential.id },
            'invitation accept declined: credential cannot be linked to the invited account',
        );
        throw new HttpError(409, 'Your account could not be linked - contact support');
    }
    return linked;
};

// Two concurrent accepts of different invitations for the same (org, account) both see no
// Membership row and both insert; the loser trips the unique index. That is "already a
// member", not a server fault.
const runAcceptTransaction = async <T>(work: (transaction: Transaction) => Promise<T>) => {
    try {
        return await sequelize.transaction(work);
    } catch (error) {
        if (error instanceof UniqueConstraintError) {
            throw new HttpError(409, 'Account is already a member of this organization');
        }
        throw error;
    }
};

// Everything that must be atomic is in one transaction: the row lock on the invitation
// (the single-use guard: a concurrent accept blocks here, then sees accepted_at set and is
// rejected), the Account link, the Membership add/revive, the accepted_at stamp and the
// audit row. org.invitation.accepted is published only after commit. If the caller is
// already an active member the add 409s and the whole transaction, including the
// accepted_at stamp, rolls back, leaving the invitation outstanding.
export const acceptInvitation = async (
    input: AcceptInvitationInput,
): Promise<AcceptInvitationResult> => {
    assertOrgsEnabled();

    const accepted = await runAcceptTransaction(async (transaction) => {
        const invitation = await verifyInvitationToken(input.token, transaction);
        if (!invitation) throw invalidInvitation();

        const credential = requireVerifiedMatchingCredential(
            await findCredentialRow(input.callerId, transaction),
            invitation.email,
        );

        const organization = await Organization.findByPk(invitation.organization_id, {
            transaction,
        });
        const inviterStillAllowed = await inviterCanStillInvite(
            invitation.organization_id,
            invitation.invited_by_account_id,
            transaction,
        );
        if (!organization || !inviterStillAllowed) throw invalidInvitation();

        const accountId = await resolveAccountId(credential, invitation.email, transaction);
        await rejectIfIssuedBeforeRemoval(invitation, accountId, transaction);
        const membership = await addMemberInTransaction(
            {
                organizationId: invitation.organization_id,
                accountId,
                role: invitation.role,
                actorAccountId: accountId,
            },
            transaction,
        );

        invitation.accepted_at = new Date();
        await invitation.save({ transaction });
        await recordAuditEvent(transaction, {
            organization_id: invitation.organization_id,
            actor_account_id: accountId,
            action: 'invitation.accepted',
            target_account_id: accountId,
            old_role: null,
            new_role: invitation.role,
        });

        await enqueueInvitationAccepted(transaction, {
            organization_id: membership.organization_id,
            account_id: accountId,
            email: invitation.email,
            role: membership.role,
            version: membership.version,
        });

        return { invitation, membership, accountId };
    });

    return {
        organization_id: accepted.membership.organization_id,
        role: accepted.membership.role,
    };
};

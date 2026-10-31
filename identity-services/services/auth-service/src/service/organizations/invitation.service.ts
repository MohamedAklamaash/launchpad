import { randomBytes, createHash } from 'node:crypto';
import type { Transaction } from 'sequelize';
import { HttpError } from '@launchpad/common';
import { Invitation } from '@/db';
import { sequelize } from '@/db/sequalize';
import { ORG_ROLE, type OrgRole } from '@/types/org-role';
import { normalizeEmail } from '@/utils/account-id';
import { assertOrgsEnabled } from './org-guard';
import { recordAuditEvent } from './audit-log.service';

// Direct copy of Infrastructure.issue_onboarding_token's pattern (CLAUDE.md: "Onboarding
// token" — single-use, SHA-256-hashed, plaintext returned exactly once). 32 random bytes
// is the same entropy budget `secrets.token_urlsafe(32)` uses on the Python side.
const INVITATION_TOKEN_BYTES = 32;
const INVITATION_TTL_MS = 7 * 24 * 60 * 60 * 1000;

export const hashToken = (plaintext: string): string =>
    createHash('sha256').update(plaintext).digest('hex');

export interface IssueInvitationInput {
    organizationId: string;
    email: string;
    role: OrgRole;
    invitedByAccountId: string;
}

export interface IssueInvitationResult {
    invitation: Invitation;
    // Plaintext token — returned exactly once, by this call, and never again. A DB read
    // of `invitations` can never recover it, only its hash.
    token: string;
}

// Security invariant (this file is the only place in the codebase that mints an
// invitation token): the token is `crypto.randomBytes`, never derived from the invitee's
// email or any account/organization id. An account id is a deterministic uuid5 of a
// normalized email (src/utils/account-id.ts) — anyone who knows the email can recompute
// it, so it must never double as a bearer credential, and neither can any other id this
// service already treats as public-ish (organization id, membership id).
export const issueInvitation = async (
    input: IssueInvitationInput,
): Promise<IssueInvitationResult> => {
    assertOrgsEnabled();
    if (input.role === ORG_ROLE.OWNER) {
        throw new HttpError(409, 'Cannot invite a member as owner');
    }

    const token = randomBytes(INVITATION_TOKEN_BYTES).toString('base64url');

    const invitation = await sequelize.transaction(async (transaction) => {
        const created = await Invitation.create(
            {
                organization_id: input.organizationId,
                email: normalizeEmail(input.email),
                role: input.role,
                invited_by_account_id: input.invitedByAccountId,
                token_hash: hashToken(token),
                expires_at: new Date(Date.now() + INVITATION_TTL_MS),
            },
            { transaction },
        );
        // target_account_id is null: the invitee has no Account row yet (that's created,
        // if needed, by accept — ticket 6). The audit trail for "who got invited" lives on
        // the Invitation row's own email column, not this table, which only has an id to
        // point at (plan/F9-teams-and-orgs.md, Decision: Data model/Audit log).
        await recordAuditEvent(transaction, {
            organization_id: input.organizationId,
            actor_account_id: input.invitedByAccountId,
            action: 'invitation.issued',
            target_account_id: null,
            old_role: null,
            new_role: input.role,
        });
        return created;
    });

    return { invitation, token };
};

export const isRedeemable = (invitation: Invitation): boolean =>
    invitation.accepted_at === null &&
    invitation.revoked_at === null &&
    invitation.expires_at.getTime() > Date.now();

// Single-use check mirrors issue_onboarding_token's callback-side verification: unused
// (never accepted or revoked) and not past its TTL. The accept flow passes its own
// transaction so the row lock is held until the Membership write commits; without one
// this is a plain read ("is this token currently redeemable").
export const verifyInvitationToken = async (
    token: string,
    transaction?: Transaction,
): Promise<Invitation | null> => {
    const invitation = await Invitation.findOne({
        where: { token_hash: hashToken(token) },
        ...(transaction ? { transaction, lock: transaction.LOCK.UPDATE } : {}),
    });
    if (!invitation || !isRedeemable(invitation)) return null;
    return invitation;
};

// organizationId is required, not optional: an invitation id alone must never be enough to
// revoke, or an admin of one org could revoke another org's invitation by id.
export const revokeInvitation = async (
    invitationId: string,
    organizationId: string,
    actorAccountId: string | null,
): Promise<void> => {
    assertOrgsEnabled();

    await sequelize.transaction(async (transaction) => {
        const invitation = await Invitation.findOne({
            where: {
                id: invitationId,
                organization_id: organizationId,
                accepted_at: null,
                revoked_at: null,
            },
            transaction,
            lock: transaction.LOCK.UPDATE,
        });
        if (!invitation) {
            throw new HttpError(404, 'Invitation not found or already used');
        }

        invitation.revoked_at = new Date();
        await invitation.save({ transaction });
        await recordAuditEvent(transaction, {
            organization_id: invitation.organization_id,
            actor_account_id: actorAccountId,
            action: 'invitation.revoked',
            target_account_id: null,
            old_role: invitation.role,
            new_role: null,
        });
    });
};

import type { Transaction } from 'sequelize';
import { MembershipAuditEvent } from '@/db';
import { logger } from '@/utils/logger';

export const AUDIT_ACTIONS = [
    'organization.created',
    'organization.renamed',
    'membership.added',
    'membership.revived',
    'membership.role_changed',
    'membership.removed',
    'membership.ownership_demoted',
    'membership.ownership_promoted',
    'invitation.issued',
    'invitation.accepted',
    'invitation.revoked',
] as const;

export type AuditAction = (typeof AUDIT_ACTIONS)[number];

const ROLE_CHANGE_ACTIONS: readonly AuditAction[] = [
    'membership.role_changed',
    'membership.ownership_demoted',
    'membership.ownership_promoted',
];

export interface AuditEventInput {
    organization_id: string;
    actor_account_id: string | null;
    action: AuditAction;
    // Null for an invitation event (issued/revoked) — the invitee has no Account yet.
    target_account_id: string | null;
    old_role: string | null;
    new_role: string | null;
}

// One structured line per committed org mutation. Account ids and roles only: no email, no
// token, no display name. `role_change` lets a log-derived metric count role changes
// without listing action names.
const logCommittedMutation = (event: AuditEventInput): void => {
    logger.info(
        {
            event: 'org_mutation',
            action: event.action,
            organization_id: event.organization_id,
            actor_account_id: event.actor_account_id,
            target_account_id: event.target_account_id,
            old_role: event.old_role,
            new_role: event.new_role,
            role_change: ROLE_CHANGE_ACTIONS.includes(event.action),
        },
        'org mutation',
    );
};

// Insert-only — every Membership/Invitation mutation writes one row here in the SAME
// transaction as the mutation itself (plan/F9-teams-and-orgs.md, Decision: Audit log), so
// the log can never drift from what actually happened. No update/delete export exists
// anywhere in this module on purpose. The log line is emitted only after the transaction
// commits, so a rolled-back mutation is never reported as having happened.
export const recordAuditEvent = async (
    transaction: Transaction,
    event: AuditEventInput,
): Promise<MembershipAuditEvent> => {
    const row = await MembershipAuditEvent.create(event, { transaction });
    transaction.afterCommit(() => logCommittedMutation(event));
    return row;
};

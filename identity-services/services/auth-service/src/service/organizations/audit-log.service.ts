import type { Transaction } from 'sequelize';
import { MembershipAuditEvent } from '@/db';

export interface AuditEventInput {
    organization_id: string;
    actor_account_id: string | null;
    action: string;
    // Null for an invitation event (issued/revoked) — the invitee has no Account yet.
    target_account_id: string | null;
    old_role: string | null;
    new_role: string | null;
}

// Insert-only — every Membership/Invitation mutation writes one row here in the SAME
// transaction as the mutation itself (plan/F9-teams-and-orgs.md, Decision: Audit log), so
// the log can never drift from what actually happened. No update/delete export exists
// anywhere in this module on purpose.
export const recordAuditEvent = (
    transaction: Transaction,
    event: AuditEventInput,
): Promise<MembershipAuditEvent> => MembershipAuditEvent.create(event, { transaction });

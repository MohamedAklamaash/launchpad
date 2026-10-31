import { Op, type Transaction } from 'sequelize';
import { Invitation } from '@/db';
import { recordAuditEvent } from './audit-log.service';

// Revokes every still-pending invitation of an org to the given addresses and writes one
// invitation.revoked audit row for each, so no invitation ever dies without a trace. Run
// inside the caller's transaction; the row lock keeps a concurrent accept from racing it.
export const revokePendingInvitations = async (
    input: { organizationId: string; emails: string[]; actorAccountId: string | null },
    transaction: Transaction,
): Promise<void> => {
    const pending = await Invitation.findAll({
        where: {
            organization_id: input.organizationId,
            email: { [Op.in]: input.emails },
            accepted_at: null,
            revoked_at: null,
        },
        transaction,
        lock: transaction.LOCK.UPDATE,
    });
    if (pending.length === 0) return;

    await Invitation.update(
        { revoked_at: new Date() },
        { where: { id: { [Op.in]: pending.map((i) => i.id) } }, transaction },
    );
    for (const invitation of pending) {
        await recordAuditEvent(transaction, {
            organization_id: input.organizationId,
            actor_account_id: input.actorAccountId,
            action: 'invitation.revoked',
            target_account_id: null,
            old_role: invitation.role,
            new_role: null,
        });
    }
};

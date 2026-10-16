import { QueryTypes, type Transaction } from 'sequelize';
import { sequelize } from '@/db/sequalize';
import type { OrgRole } from '@/types/org-role';

interface MembershipVersionRow {
    version: number;
}

// The host_readiness_version pattern (deployment-services/infrastructure-service/api/
// services/host_readiness.py), ported to this service: one UPDATE ... RETURNING claims
// Postgres's own row lock and increments the counter atomically, so two concurrent writers
// against the same Membership row can never observe (or publish) the same resulting
// version — no separate SELECT ... FOR UPDATE round trip needed, same reasoning as
// otp-attempts.ts's claimAttempt. Callers pass the already-open transaction so this
// composes with the audit-event insert they write in the same transaction.
export const bumpMembershipVersion = async (
    membershipId: string,
    newRole: OrgRole,
    transaction: Transaction,
): Promise<number> => {
    const rows = await sequelize.query<MembershipVersionRow>(
        `UPDATE memberships
            SET role = :role, version = version + 1, updated_at = NOW()
          WHERE id = :membershipId
          RETURNING version`,
        {
            replacements: { membershipId, role: newRole },
            type: QueryTypes.SELECT,
            transaction,
        },
    );
    const row = rows[0];
    if (!row) {
        throw new Error(`Membership ${membershipId} not found`);
    }
    return row.version;
};

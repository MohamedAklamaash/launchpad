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

// Removal tombstones the row instead of deleting it (removeMember's half of the fix for
// the resurrection race: a stale org.membership.changed redelivered after
// org.membership.removed must have no row left to recreate). Keeps `role` as its last
// known value — the tombstone is kept for audit/history, not read for authorization —
// and bumps `version` the same way bumpMembershipVersion does, so a consumer's
// version-rejection rule applies uniformly across role changes and removals.
export const bumpMembershipVersionForRemoval = async (
    membershipId: string,
    transaction: Transaction,
): Promise<number> => {
    const rows = await sequelize.query<MembershipVersionRow>(
        `UPDATE memberships
            SET removed_at = NOW(), version = version + 1, updated_at = NOW()
          WHERE id = :membershipId
          RETURNING version`,
        {
            replacements: { membershipId },
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

// Revives a tombstoned row (addMember's re-add path) — clears removed_at and sets the new
// role, bumping version off whatever it last stopped at rather than restarting at 1. This
// is what keeps the (organization_id, account_id) version sequence monotonic across a
// remove-then-re-add cycle, which is the whole point: a stale event from before the
// removal can never outrank the revived row.
export const reviveMembershipVersion = async (
    membershipId: string,
    newRole: OrgRole,
    transaction: Transaction,
): Promise<number> => {
    const rows = await sequelize.query<MembershipVersionRow>(
        `UPDATE memberships
            SET role = :role, removed_at = NULL, version = version + 1, updated_at = NOW()
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

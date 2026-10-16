import { sequelize } from '@/db/sequalize';
import { DataTypes, Model, type Optional } from 'sequelize';
import { v7 as uuidv7 } from 'uuid';

export interface MembershipAuditEventAttributes {
    id: string;
    organization_id: string;
    // Null only for a system-initiated action (e.g. the personal-org backfill creating the
    // owner membership with no human actor) — every actor-initiated mutation sets this.
    actor_account_id: string | null;
    action: string;
    // Nullable: an invitation event (issued/revoked) has no Account yet — the invitee is
    // identified only by email at that point (plan/F9-teams-and-orgs.md's Decision: Audit
    // log table doesn't carry an email column, so an invitation audit row records the
    // action/role/org but not who it targets until accept creates the Account; see
    // invitation.service.ts). Every Membership-row mutation still always sets this.
    target_account_id: string | null;
    old_role: string | null;
    new_role: string | null;
    occurred_at: Date;
}

export type MembershipAuditEventCreationAttributes = Optional<
    MembershipAuditEventAttributes,
    'id' | 'occurred_at' | 'actor_account_id' | 'target_account_id' | 'old_role' | 'new_role'
>;

// Append-only: this model intentionally exposes no update/delete path anywhere in the
// service layer (plan/F9-teams-and-orgs.md, Decision: Audit log — "insert-only API...
// which is itself the integrity guarantee"). Every Membership/Invitation mutation inserts
// exactly one row here in the same transaction as the mutation.
export class MembershipAuditEvent extends Model<
    MembershipAuditEventAttributes,
    MembershipAuditEventCreationAttributes
> {
    declare id: string;
    declare organization_id: string;
    declare actor_account_id: string | null;
    declare action: string;
    declare target_account_id: string | null;
    declare old_role: string | null;
    declare new_role: string | null;
    declare occurred_at: Date;
}

MembershipAuditEvent.init(
    {
        id: {
            type: DataTypes.UUID,
            defaultValue: () => uuidv7(),
            primaryKey: true,
        },
        organization_id: {
            type: DataTypes.UUID,
            allowNull: false,
        },
        actor_account_id: {
            type: DataTypes.UUID,
            allowNull: true,
        },
        action: {
            type: DataTypes.STRING,
            allowNull: false,
        },
        target_account_id: {
            type: DataTypes.UUID,
            allowNull: true,
        },
        old_role: {
            type: DataTypes.STRING,
            allowNull: true,
        },
        new_role: {
            type: DataTypes.STRING,
            allowNull: true,
        },
        occurred_at: {
            type: DataTypes.DATE,
            allowNull: false,
            defaultValue: DataTypes.NOW,
        },
    },
    {
        sequelize,
        tableName: 'membership_audit_events',
        timestamps: false,
    },
);

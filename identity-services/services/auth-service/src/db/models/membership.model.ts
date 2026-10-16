import { sequelize } from '@/db/sequalize';
import { DataTypes, Model, type Optional } from 'sequelize';
import { v7 as uuidv7 } from 'uuid';
import { ORG_ROLE, ORG_ROLE_VALUES, type OrgRole } from '@/types/org-role';

export interface MembershipAttributes {
    id: string;
    organization_id: string;
    account_id: string;
    role: OrgRole;
    // Per-row monotonic counter, incremented under a row lock on every write
    // (src/utils/membership-version.ts) — the host_readiness_version pattern F1b already
    // proved: every org.events payload carries it, and a consumer rejects any payload
    // whose version isn't strictly greater than what it has stored.
    version: number;
    created_at: Date;
    updated_at: Date;
}

export type MembershipCreationAttributes = Optional<
    MembershipAttributes,
    'id' | 'version' | 'created_at' | 'updated_at'
>;

export class Membership extends Model<MembershipAttributes, MembershipCreationAttributes> {
    declare id: string;
    declare organization_id: string;
    declare account_id: string;
    declare role: OrgRole;
    declare version: number;
    declare created_at: Date;
    declare updated_at: Date;
}

Membership.init(
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
        account_id: {
            type: DataTypes.UUID,
            allowNull: false,
        },
        role: {
            type: DataTypes.ENUM(...ORG_ROLE_VALUES),
            allowNull: false,
            defaultValue: ORG_ROLE.VIEWER,
        },
        version: {
            type: DataTypes.INTEGER,
            allowNull: false,
            defaultValue: 1,
        },
        created_at: {
            type: DataTypes.DATE,
            allowNull: false,
            defaultValue: DataTypes.NOW,
        },
        updated_at: {
            type: DataTypes.DATE,
            allowNull: false,
            defaultValue: DataTypes.NOW,
        },
    },
    {
        sequelize,
        tableName: 'memberships',
        timestamps: true,
        createdAt: 'created_at',
        updatedAt: 'updated_at',
        indexes: [
            // One membership per (org, account) — plain unique, created by sync() since
            // `memberships` is a brand-new table. The "exactly one owner per org"
            // invariant is a SEPARATE partial index, deliberately NOT declared here: it
            // is added in db/index.ts's applyPendingSchemaPatches as raw SQL so there is
            // one source of truth for its definition (and the test can assert that exact
            // SQL) rather than two differently-named indexes doing almost the same thing.
            {
                name: 'memberships_organization_id_account_id_unique',
                unique: true,
                fields: ['organization_id', 'account_id'],
            },
        ],
    },
);

import { sequelize } from '@/db/sequalize';
import { DataTypes, Model, type Optional } from 'sequelize';
import { v7 as uuidv7 } from 'uuid';
import { USER_ROLE } from '@/types/auth.invited_user.types';

export interface UserAttributes {
    id: string;
    email: string;
    user_name: string;
    role: string;
    profile_url?: string;
    infra_id: string[];
    created_at: Date;
    updated_at: Date;
    metadata: Record<string, unknown>;
    // F9 ticket 1: additive, nullable identity link to the new accounts table. Backfilled
    // by src/scripts/backfill-accounts.ts; no code reads it yet (ORGS_ENABLED gates every
    // behavior change that will, starting with later F9 tickets).
    account_id: string | null;
}

export type UserCreationAttributes = Optional<
    UserAttributes,
    'id' | 'created_at' | 'updated_at' | 'metadata' | 'profile_url' | 'infra_id' | 'account_id'
>;

export class User extends Model<UserAttributes, UserCreationAttributes> {
    declare id: string;
    declare email: string;
    declare user_name: string;
    declare role: string;
    declare profile_url?: string;
    declare infra_id: string[];
    declare created_at: Date;
    declare updated_at: Date;
    declare metadata: Record<string, unknown>;
    declare account_id: string | null;
}

User.init(
    {
        id: {
            type: DataTypes.UUID,
            defaultValue: () => uuidv7(),
            primaryKey: true,
        },
        email: {
            type: DataTypes.STRING,
            allowNull: false,
            unique: true,
        },
        user_name: {
            type: DataTypes.STRING,
            allowNull: false,
            unique: true,
        },
        role: {
            type: DataTypes.STRING,
            allowNull: false,
            defaultValue: USER_ROLE.ADMIN,
        },
        profile_url: {
            type: DataTypes.STRING,
            allowNull: true,
        },
        infra_id: {
            type: DataTypes.ARRAY(DataTypes.UUID),
            allowNull: false,
            defaultValue: [],
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
        metadata: {
            type: DataTypes.JSON,
            allowNull: true,
        },
        account_id: {
            type: DataTypes.UUID,
            allowNull: true,
        },
    },
    {
        sequelize,
        tableName: 'users',
        timestamps: true,
        createdAt: 'created_at',
        updatedAt: 'updated_at',
    },
);

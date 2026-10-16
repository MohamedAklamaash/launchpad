import { sequelize } from '@/db/sequalize';
import { DataTypes, Model, type Optional } from 'sequelize';
import { v7 as uuidv7 } from 'uuid';

export interface OrganizationAttributes {
    id: string;
    owner_account_id: string;
    name: string;
    created_at: Date;
    updated_at: Date;
}

export type OrganizationCreationAttributes = Optional<
    OrganizationAttributes,
    'id' | 'created_at' | 'updated_at'
>;

export class Organization extends Model<OrganizationAttributes, OrganizationCreationAttributes> {
    declare id: string;
    declare owner_account_id: string;
    declare name: string;
    declare created_at: Date;
    declare updated_at: Date;
}

Organization.init(
    {
        id: {
            // Personal-org backfill (ticket 3+) overrides this default with
            // uuid5(ORG_NAMESPACE, legacy_user_id) so every mirroring service agrees on
            // the id independently. Organic orgs created later (e.g. a team org) have no
            // prior row to agree with, so they fall back to this default, same as Account.
            type: DataTypes.UUID,
            defaultValue: () => uuidv7(),
            primaryKey: true,
        },
        owner_account_id: {
            type: DataTypes.UUID,
            allowNull: false,
        },
        name: {
            type: DataTypes.STRING,
            allowNull: false,
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
        tableName: 'organizations',
        timestamps: true,
        createdAt: 'created_at',
        updatedAt: 'updated_at',
    },
);

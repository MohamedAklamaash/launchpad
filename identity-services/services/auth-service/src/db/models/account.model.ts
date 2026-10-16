import { sequelize } from '@/db/sequalize';
import { DataTypes, Model, type Optional } from 'sequelize';
import { v7 as uuidv7 } from 'uuid';

export interface AccountAttributes {
    id: string;
    email: string;
    display_name: string | null;
    created_at: Date;
}

export type AccountCreationAttributes = Optional<
    AccountAttributes,
    'id' | 'created_at' | 'display_name'
>;

export class Account extends Model<AccountAttributes, AccountCreationAttributes> {
    declare id: string;
    declare email: string;
    declare display_name: string | null;
    declare created_at: Date;
}

Account.init(
    {
        id: {
            // F9 ticket 1's backfill overrides this default, passing an id computed by
            // accountIdForEmail (uuid5, deterministic) so legacy rows agree on one Account
            // id per normalized email. Organic rows created by later tickets (OAuth signup,
            // invite accept) have no prior row to agree with, so they fall back to this
            // default, same as every other table here.
            type: DataTypes.UUID,
            defaultValue: () => uuidv7(),
            primaryKey: true,
        },
        email: {
            type: DataTypes.STRING,
            allowNull: false,
            unique: true,
        },
        display_name: {
            type: DataTypes.STRING,
            allowNull: true,
        },
        created_at: {
            type: DataTypes.DATE,
            allowNull: false,
            defaultValue: DataTypes.NOW,
        },
    },
    {
        sequelize,
        tableName: 'accounts',
        timestamps: false,
    },
);

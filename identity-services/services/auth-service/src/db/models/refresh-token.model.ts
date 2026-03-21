import { DataTypes, Model, type Optional } from 'sequelize';
import { sequelize } from '@/db/sequalize';
import { v7 as uuidv7 } from 'uuid';

export interface RefreshTokenAttributes {
    id: string;
    user_id: string;
    token_id: string;
    expires_at: Date;
    created_at: Date;
    updated_at: Date;
}

export type RefreshTokenCreationAttributes = Optional<
    RefreshTokenAttributes,
    'id' | 'created_at' | 'updated_at'
>;

export class RefreshToken extends Model<RefreshTokenAttributes, RefreshTokenCreationAttributes> {
    declare id: string;
    declare user_id: string;
    declare token_id: string;
    declare expires_at: Date;
    declare created_at: Date;
    declare updated_at: Date;
}

RefreshToken.init(
    {
        id: {
            type: DataTypes.UUID,
            defaultValue: () => uuidv7(),
            primaryKey: true,
        },
        user_id: {
            type: DataTypes.STRING,
            allowNull: false,
        },
        token_id: {
            type: DataTypes.STRING,
            allowNull: false,
        },
        expires_at: {
            type: DataTypes.DATE,
            allowNull: false,
            defaultValue: sequelize.literal("NOW() + INTERVAL '7 days'"),
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
        tableName: 'refresh_tokens',
    },
);

// InvitedUser.hasMany(RefreshToken, { foreignKey: "user_id", as: "refresh_tokens", onDelete: "CASCADE", constraints: false });
// RefreshToken.belongsTo(InvitedUser, { foreignKey: "user_id", as: "invited_user", constraints: false });

// User.hasMany(RefreshToken, { foreignKey: "user_id", as: "refresh_tokens", onDelete: "CASCADE", constraints: false });
// RefreshToken.belongsTo(User, { foreignKey: "user_id", as: "user", constraints: false });

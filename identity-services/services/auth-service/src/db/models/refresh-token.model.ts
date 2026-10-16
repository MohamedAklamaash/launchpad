import { DataTypes, Model, type Optional } from 'sequelize';
import { sequelize } from '@/db/sequalize';
import { v7 as uuidv7 } from 'uuid';

export interface RefreshTokenAttributes {
    id: string;
    user_id: string;
    token_id: string;
    expires_at: Date;
    // F9 ticket 5: the org a refreshed access token should carry. Set at creation to the
    // caller's personal org (or an explicit override — switch-org's rotation path) and
    // updated only by switch-org; /auth/refresh must read this rather than defaulting to
    // the personal org on every rotation, which would silently undo an org switch on the
    // next token refresh (plan/F9-teams-and-orgs.md, Decision: JWT and authorization).
    // Nullable only for a row created before this column existed — refresh() backfills it
    // on first use after upgrade (see invited-user.auth.service.ts).
    active_org_id: string | null;
    created_at: Date;
    updated_at: Date;
}

export type RefreshTokenCreationAttributes = Optional<
    RefreshTokenAttributes,
    'id' | 'active_org_id' | 'created_at' | 'updated_at'
>;

export class RefreshToken extends Model<RefreshTokenAttributes, RefreshTokenCreationAttributes> {
    declare id: string;
    declare user_id: string;
    declare token_id: string;
    declare expires_at: Date;
    declare active_org_id: string | null;
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
        active_org_id: {
            type: DataTypes.UUID,
            allowNull: true,
            defaultValue: null,
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

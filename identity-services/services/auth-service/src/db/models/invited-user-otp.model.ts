import { sequelize } from '@/db/sequalize';
import { DataTypes, Model, type Optional } from 'sequelize';
import { v7 as uuidv7 } from 'uuid';

import { InvitedUser } from '@/db/models/invited-user.model';

export interface UserOTPAttributes {
    id: string;
    invited_user_id: string;
    otp: string;
    expires_at: Date;
    infra_id: string;
    // Which flow minted this code — 'register' or 'password-reset' (see
    // invited-user.auth.service.ts / invited-user.password.crud.service.ts). Without
    // this, a code minted for one flow could be redeemed through the other: a
    // password-reset code submitted to authenticate-with-otp would still match
    // (same invited_user_id, same otp value) and mint a full session, even though the
    // holder only ever proved "I can read this inbox" for a reset, not a login.
    purpose: string;
}

export type UserOTPCreationAttributes = Optional<UserOTPAttributes, 'id' | 'expires_at'>;

export class UserOTP extends Model<UserOTPAttributes, UserOTPCreationAttributes> {
    declare id: string;
    declare invited_user_id: string;
    declare otp: string;
    declare expires_at: Date;
    declare infra_id: string;
    declare purpose: string;
}

UserOTP.init(
    {
        id: {
            type: DataTypes.UUID,
            defaultValue: () => uuidv7(),
            primaryKey: true,
        },
        invited_user_id: {
            type: DataTypes.UUID,
            allowNull: false,
            references: {
                model: 'invited_users',
                key: 'id',
            },
        },
        otp: {
            type: DataTypes.STRING,
            allowNull: false,
        },
        infra_id: {
            type: DataTypes.UUID,
            allowNull: false,
        },
        // Default only matters for the startup ALTER's backfill of pre-existing rows
        // (db/index.ts) — any row from before this column existed is already past its
        // 10-minute expiry by the time this ships, so the exact backfilled value can
        // never actually be redeemed either way.
        purpose: {
            type: DataTypes.STRING,
            allowNull: false,
            defaultValue: 'register',
        },
        expires_at: {
            type: DataTypes.DATE,
            allowNull: false,
            defaultValue: sequelize.literal("NOW() + INTERVAL '5 minutes'"),
        },
    },
    {
        sequelize,
        tableName: 'invited_user_otp',
    },
);

UserOTP.hasOne(InvitedUser, {
    foreignKey: 'invited_user_id',
    as: 'invited_user',
    onDelete: 'CASCADE',
});

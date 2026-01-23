import { sequelize } from "@/db/sequalize";
import { DataTypes, Model, type Optional } from "sequelize";
import { v7 as uuidv7 } from "uuid";

import { InvitedUser } from "@/db/models/invited-user.model";

export interface UserOTPAttributes {
    id: string;
    invited_user_id: string;
    otp: string;
    expires_at: Date;
}

export type UserOTPCreationAttributes = Optional<
    UserOTPAttributes,
    "id" | "expires_at"
>;

export class UserOTP extends Model<UserOTPAttributes, UserOTPCreationAttributes> {
    declare id: string;
    declare invited_user_id: string;
    declare otp: string;
    declare expires_at: Date;
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
                model: "invited_users",
                key: "id",
            }
        },
        otp: {
            type: DataTypes.STRING,
            allowNull: false,
        },
        expires_at: {
            type: DataTypes.DATE,
            allowNull: false,
            defaultValue: sequelize.literal("NOW() + INTERVAL '5 minutes'"),
        },
    },
    {
        sequelize,
        tableName: "invited_user_otp",
    }
)

UserOTP.hasOne(InvitedUser, { foreignKey: "invited_user_id", as: "invited_user", onDelete: "CASCADE" });
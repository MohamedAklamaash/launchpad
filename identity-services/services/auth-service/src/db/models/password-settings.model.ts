import { sequelize } from "@/db/sequalize";
import { DataTypes, Model } from "sequelize";

import { InvitedUser } from "@/db/models/invited-user.model";

export interface PasswordSettingsAttributes {
    invited_user_id: string;
    expires_at: Date; // we can let the admin user set the expiration of the password
}

export class PasswordSettings extends Model<PasswordSettingsAttributes> {
    declare invited_user_id: string;
    declare expires_at: Date;
}

PasswordSettings.init(
    {
        invited_user_id: {
            type: DataTypes.UUID,
            allowNull: false,
            references: {
                model: "invited_users",
                key: "id",
            },
        },
        expires_at: {
            type: DataTypes.DATE,
            allowNull: false,
            defaultValue: sequelize.literal("NOW() + INTERVAL '7 days'"),
        }
    },
    {
        sequelize,
        tableName: "password_settings",
    }
)

PasswordSettings.hasOne(InvitedUser, { foreignKey: "invited_user_id", as: "invited_user", onDelete: "CASCADE" });

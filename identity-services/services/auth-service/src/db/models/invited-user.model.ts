import { sequelize } from "@/db/sequalize";
import { USER_ROLE } from "@/types/auth.invited_user.types";
import { DataTypes, Model, type Optional } from "sequelize";
import { v7 as uuidv7 } from "uuid";

export interface InvitedUserAttributes {
    id: string,
    infra_id: string[],
    email: string,
    user_name: string,
    password_hash: string,
    role: USER_ROLE,
    forgot_password: boolean,
    is_authenticated: boolean,
    opt_id: string,
    created_at: Date,
    updated_at: Date,
}

export type InvitedUserCreationAttributes = Optional<
    InvitedUserAttributes,
    "id" | "created_at" | "updated_at" | "forgot_password" | "is_authenticated" | "opt_id"
>;

export class InvitedUser extends Model<InvitedUserAttributes, InvitedUserCreationAttributes> {
    declare id: string;
    declare infra_id: string[];
    declare email: string;
    declare user_name: string;
    declare password_hash: string;
    declare role: USER_ROLE;
    declare forgot_password: boolean;
    declare is_authenticated: boolean;
    declare opt_id: string;
    declare created_at: Date;
    declare updated_at: Date;
}

InvitedUser.init(
    {
        id: {
            type: DataTypes.UUID,
            defaultValue: () => uuidv7(),
            primaryKey: true,
        },
        infra_id: {
            type: DataTypes.ARRAY(DataTypes.UUID),
            allowNull: false,
        },
        email: {
            type: DataTypes.STRING,
            allowNull: false,
            unique: true,
        },
        user_name: {
            type: DataTypes.STRING,
            allowNull: false,
        },
        password_hash: {
            type: DataTypes.STRING,
            allowNull: false,
        },
        role: {
            type: DataTypes.ENUM(...Object.values(USER_ROLE)),
            allowNull: false,
        },
        forgot_password: {
            type: DataTypes.BOOLEAN,
            allowNull: false,
            defaultValue: false,
        },
        is_authenticated: {
            type: DataTypes.BOOLEAN,
            allowNull: false,
            defaultValue: false,
        },
        opt_id: {
            type: DataTypes.UUID,
            allowNull: true,
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
        tableName: "invited_users",
        indexes: [
            {
                name: "invited_users_user_name_idx", // we can use gin_trgm_ops for partial matching later to handle type tolerance
                fields: ["user_name"],
                using: "BTREE",
            },
        ],
    }
)
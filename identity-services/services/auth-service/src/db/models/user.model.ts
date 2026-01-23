import { sequelize } from "@/db/sequalize";
import { DataTypes, Model, type Optional } from "sequelize";
import { v7 as uuidv7 } from "uuid";

export interface UserAttributes {
    id: string;
    email: string;
    user_name: string;
    profile_url?: string;
    created_at: Date;
    updated_at: Date;
    metadata: Record<string, unknown>;
}

export type UserCreationAttributes = Optional<
    UserAttributes,
    "id" | "created_at" | "updated_at" | "metadata" | "profile_url"
>;

export class User extends Model<UserAttributes, UserCreationAttributes> {
    declare id: string;
    declare email: string;
    declare user_name: string;
    declare profile_url?: string;
    declare createdAt: Date;
    declare updatedAt: Date;
    declare metadata: Record<string, unknown>;
}

User.init({
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
    profile_url: {
        type: DataTypes.STRING,
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
    metadata: {
        type: DataTypes.JSON,
        allowNull: true,
    },
}, {
    sequelize,
    tableName: "users",
    timestamps: true,
    createdAt: "createdAt",
    updatedAt: "updatedAt",
});

import { DataTypes, Model } from 'sequelize';
import { sequelize } from '@/db/sequalize';

import type { User as Usermodel } from '@/types/user.type';

export class User extends Model<Usermodel> implements Usermodel {
    declare user_id: string;
    declare user_name: string;
    declare role: string;
    declare email: string;
    declare profile_url?: string;
    declare created_at: Date;
    declare updated_at: Date;
    declare metadata?: Record<string, unknown>;
    declare infra_id: string[];
    declare invited_by?: string;
}

User.init(
    {
        user_id: {
            type: DataTypes.STRING,
            allowNull: false,
            primaryKey: true,
        },
        user_name: {
            type: DataTypes.STRING,
            allowNull: false,
        },
        role: {
            type: DataTypes.STRING,
            allowNull: false,
        },
        email: {
            type: DataTypes.STRING,
            allowNull: false,
        },
        profile_url: {
            type: DataTypes.STRING,
            allowNull: true,
        },
        created_at: {
            type: DataTypes.DATE,
            allowNull: false,
        },
        updated_at: {
            type: DataTypes.DATE,
            allowNull: false,
        },
        metadata: {
            type: DataTypes.JSON,
            allowNull: true,
        },
        infra_id: {
            type: DataTypes.JSON,
            allowNull: false,
        },
        invited_by: {
            type: DataTypes.STRING,
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

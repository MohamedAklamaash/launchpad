import { Op, type WhereOptions } from 'sequelize';

import type { AuthUserRegisteredPayload } from '@launchpad/common'
import { User as UserModel } from "@/db";
import type { User as IUser, CreateUserInput } from "@/types/user.type";

const toUserSignature = (user: UserModel): IUser => {
    return {
        user_id: user.user_id,
        email: user.email,
        role: user.role,
        created_at: user.created_at,
        updated_at: user.updated_at,
        infra_id: user.infra_id,
        metadata: user.metadata,
        user_name: user.user_name,
        profile_url: user.profile_url,
    }
}

export class UserRepository {
    async findbyid(id: string): Promise<IUser | null> {
        const user = await UserModel.findByPk(id)
        return user ? toUserSignature(user) : null
    }

    async findAll(): Promise<IUser[]> {
        const users = await UserModel.findAll({
            order: [['created_at', 'DESC']],
        });
        return users.map(toUserSignature);
    }

    async upsertFromAuthEvent(payload: AuthUserRegisteredPayload): Promise<IUser> {
        const [user] = await UserModel.upsert(
            {
                user_id: payload.id,
                email: payload.email,
                role: payload.role,
                created_at: new Date(payload.created_at),
                updated_at: new Date(),
                infra_id: payload.infra_id,
                metadata: payload.metadata,
                user_name: payload.user_name,
            },
            { returning: true },
        );

        return toUserSignature(user);
    }

    async create(input: CreateUserInput): Promise<IUser> {
        const user = await UserModel.create({
            user_id: input.user_id,
            email: input.email,
            user_name: input.user_name,
            role: input.role,
            infra_id: input.infra_id,
            profile_url: input.profile_url,
            metadata: input.metadata,
            created_at: new Date(),
            updated_at: new Date()
        });
        return toUserSignature(user);
    }

    async searchByQuery(
        query: string,
        options: { limit?: number; excludeIds?: string[] } = {},
    ): Promise<IUser[]> {
        const where: WhereOptions = {
            [Op.or]: [
                { user_name: { [Op.like]: `%${query}%` } },
                { email: { [Op.like]: `%${query}%` } },
            ],
        };

        if (options.excludeIds && options.excludeIds.length > 0) {
            Object.assign(where, {
                [Op.and]: [{ user_id: { [Op.notIn]: options.excludeIds } }],
            });
        }

        const users = await UserModel.findAll({
            where,
            order: [['created_at', 'DESC']],
            limit: options.limit ?? 10,
        });

        return users.map(toUserSignature);
    }
}

export const userRepository = new UserRepository()
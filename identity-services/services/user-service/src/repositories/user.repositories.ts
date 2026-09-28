import { Op, type WhereOptions } from 'sequelize';
import { InfraCreatedPayload } from '@launchpad/common';

import type { AuthUserRegisteredPayload } from '@launchpad/common';
import { User as UserModel, sequelize } from '@/db';
import type { User as IUser, CreateUserInput } from '@/types/user.type';

// MySQL's LIKE treats `%` and `_` as wildcards and `\` as its escape character —
// unescaped, a caller's own query text changes what the pattern matches (e.g. `___`
// matches any 3+ character name, defeating the "must know something specific" point of
// the minimum query length upstream).
const escapeLikePattern = (value: string): string =>
    value.replace(/[\\%_]/g, (char) => `\\${char}`);

// infra_id is a UUID (see InvitedUser.infra_id, auth-service) — validating the shape
// before splicing it into a raw SQL fragment (below) means there is no character set
// left that could break out of the string literal.
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

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
        invited_by: user.invited_by,
    };
};

export class UserRepository {
    async findbyid(id: string): Promise<IUser | null> {
        const user = await UserModel.findByPk(id);
        return user ? toUserSignature(user) : null;
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
                updated_at: payload.updated_at ? new Date(payload.updated_at) : new Date(),
                infra_id: payload.infra_id,
                metadata: payload.metadata,
                user_name: payload.user_name,
                invited_by: payload.invited_by,
            },
            { returning: true },
        );

        return toUserSignature(user);
    }

    async syncInfraCreation(payload: InfraCreatedPayload): Promise<void> {
        const user = await UserModel.findByPk(payload.user_id);
        if (user) {
            const currentInfraIds = user.infra_id || [];
            if (!currentInfraIds.includes(payload.infra_id)) {
                user.infra_id = [...currentInfraIds, payload.infra_id];
                await user.save();
            }
        }
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
            invited_by: input.invited_by,
            created_at: new Date(),
            updated_at: new Date(),
        });
        return toUserSignature(user);
    }

    async searchByQuery(
        query: string,
        options: { limit?: number; excludeIds?: string[]; infraIds?: string[] } = {},
    ): Promise<IUser[]> {
        const escaped = escapeLikePattern(query);
        const conditions: WhereOptions[] = [
            {
                [Op.or]: [
                    { user_name: { [Op.like]: `%${escaped}%` } },
                    { email: { [Op.like]: `%${escaped}%` } },
                ],
            },
        ];

        if (options.excludeIds && options.excludeIds.length > 0) {
            conditions.push({ user_id: { [Op.notIn]: options.excludeIds } });
        }

        // Scoped to users who share at least one infra with the caller, filtered in SQL
        // rather than over-fetching and narrowing in JS — a JS post-filter on a capped
        // set of name/email matches can starve real matches that don't happen to land
        // in the first page of that raw query.
        if (options.infraIds) {
            const safeInfraIds = options.infraIds.filter((id) => UUID_RE.test(id));
            if (safeInfraIds.length === 0) return [];
            conditions.push({
                [Op.or]: safeInfraIds.map((id) =>
                    sequelize.literal(`JSON_CONTAINS(infra_id, '"${id}"')`),
                ),
            } as unknown as WhereOptions);
        }

        const users = await UserModel.findAll({
            where: { [Op.and]: conditions },
            order: [['created_at', 'DESC']],
            limit: options.limit ?? 10,
        });

        return users.map(toUserSignature);
    }
}

export const userRepository = new UserRepository();

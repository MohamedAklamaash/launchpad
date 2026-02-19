import { userRepository, type UserRepository } from "@/repositories/user.repositories";
import { AuthUserRegisteredPayload, HttpError, InfraCreatedPayload } from '@launchpad/common';
import { UniqueConstraintError } from 'sequelize';
import { publishUserCreatedEvent } from "@/messaging/consumer/user.consumer";
import { User, CreateUserInput } from "@/types/user.type";

class UserService {
    constructor(private readonly repository: UserRepository) { }

    async getUserById(id: string): Promise<User> {
        const user = await this.repository.findbyid(id);
        if (!user) {
            throw new HttpError(404, 'User not found');
        }
        return user;
    }

    async getAllUsers(): Promise<User[]> {
        return this.repository.findAll();
    }

    async createUser(input: CreateUserInput): Promise<User> {
        try {
            const user = await this.repository.create(input);

            void publishUserCreatedEvent({
                id: user.user_id,
                email: user.email,
                user_name: user.user_name,
                created_at: user.created_at,
                infra_id: user.infra_id,
                role: user.role,
                profile_url: user.profile_url,
                metadata: user.metadata,
                updated_at: user.updated_at,
                invited_by: user.invited_by
            });

            return user;
        } catch (error) {
            if (error instanceof UniqueConstraintError) {
                throw new HttpError(409, 'User already exists');
            }
            throw error;
        }
    }

    async searchUsers(params: {
        query: string;
        limit?: number;
        excludeIds?: string[];
    }): Promise<User[]> {
        const query = params.query.trim();
        if (query.length === 0) {
            return [];
        }

        return this.repository.searchByQuery(query, {
            limit: params.limit,
            excludeIds: params.excludeIds,
        });
    }

    async syncFromAuthUser(payload: AuthUserRegisteredPayload): Promise<User> {
        const user = await this.repository.upsertFromAuthEvent(payload);
        return user;
    }

    async syncInfraCreation(payload: InfraCreatedPayload): Promise<void> {
        await this.repository.syncInfraCreation(payload);
    }
}

export const userService = new UserService(userRepository);
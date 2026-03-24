import { githubHttpClient } from '@/utils/http-client';
import { User } from '@/db';
import { sequelize } from '@/db/sequalize';
import { env } from '@/config/env';
import {
    GithubCallbackInput,
    GithubUserUpsertInput,
    GithubTokenResponse,
    GithubUserResponse,
    GithubEmail,
} from '@/types/auth.user.types';
import { PublishUserRegistered } from '@/messaging/producer/user-created.message';
import { BaseService } from '@/service/invited-users/invited-user.base.service';
import { USER_ROLE } from '@/types/auth.invited_user.types';
import { verifyAccessToken } from '@/utils/handle-token';

export class UserFacadeService extends BaseService {
    private clientId = env.GITHUB_CLIENT_ID;
    private clientSecret = env.GITHUB_CLIENT_SECRET;
    private redirectUri = env.GITHUB_REDIRECT_URI;

    public getAuthUrl(): string {
        return `https://github.com/login/oauth/authorize?client_id=${this.clientId}&redirect_uri=${this.redirectUri}&scope=repo%20read:org%20user:email`;
    }

    public async getUserFromToken(token: string) {
        const payload = verifyAccessToken(token);
        const user = await User.findByPk(payload.sub);

        if (!user) {
            throw new Error('User not found');
        }

        return {
            id: user.id,
            email: user.email,
            user_name: user.user_name,
            role: user.role,
            profile_url: user.profile_url,
            infra_id: user.infra_id || [],
            metadata: user.metadata,
            created_at: user.created_at,
            updated_at: user.updated_at,
        };
    }

    public async handleCallback(input: GithubCallbackInput) {
        const { code } = input;
        const tokenRes = await githubHttpClient.post(
            'https://github.com/login/oauth/access_token',
            {
                client_id: this.clientId,
                client_secret: this.clientSecret,
                code,
                redirect_uri: this.redirectUri,
            },
            { headers: { Accept: 'application/json' } },
        );

        const token = (tokenRes.data as GithubTokenResponse).access_token;
        if (!token) throw new Error('Failed to get GitHub token');

        const userRes = await githubHttpClient.get('https://api.github.com/user', {
            headers: {
                Authorization: `Bearer ${token}`,
                Accept: 'application/vnd.github+json',
                'X-GitHub-Api-Version': '2026-03-10',
            },
        });

        const {
            login: username,
            id: github_id,
            avatar_url,
            email,
        } = userRes.data as GithubUserResponse;
        const githubIdStr = String(github_id);

        let resolvedEmail = email;
        if (!resolvedEmail) {
            const emailsRes = await githubHttpClient.get('https://api.github.com/user/emails', {
                headers: {
                    Authorization: `Bearer ${token}`,
                    Accept: 'application/vnd.github+json',
                    'X-GitHub-Api-Version': '2026-03-10',
                },
            });
            const primary = (emailsRes.data as GithubEmail[]).find((e) => e.primary && e.verified);
            resolvedEmail = primary?.email ?? null;
        }

        return { token, username, github_id: githubIdStr, avatar_url, email: resolvedEmail };
    }

    public async upsertUser(githubData: GithubUserUpsertInput) {
        return sequelize.transaction(async (transaction) => {
            let user = await User.findOne({
                where: sequelize.where(
                    sequelize.cast(sequelize.json('metadata.github.id'), 'text'),
                    githubData.github_id,
                ),
                transaction,
            });

            if (!user && githubData.email) {
                user = await User.findOne({ where: { email: githubData.email }, transaction });
            }

            const githubMetadata = {
                id: githubData.github_id,
                token: githubData.token,
                profile_url: githubData.avatar_url,
                email: githubData.email,
                username: githubData.username,
            };

            if (user) {
                user.metadata = {
                    ...(user.metadata || {}),
                    github: githubMetadata,
                };
                user.profile_url = githubData.avatar_url;
                // Backfill real email if stored email is the github.com placeholder
                if (githubData.email && user.email.endsWith('@github.com')) {
                    user.email = githubData.email;
                }
                await user.save({ transaction });
            } else {
                user = await User.create(
                    {
                        user_name: githubData.username,
                        profile_url: githubData.avatar_url,
                        email:
                            githubData.email ?? `${githubData.github_id}@users.noreply.github.com`,
                        role: USER_ROLE.SUPER_ADMIN, // GitHub users are SUPER_ADMIN
                        infra_id: [],
                        metadata: {
                            github: githubMetadata,
                        },
                    },
                    { transaction },
                );
            }

            PublishUserRegistered({
                id: user.id,
                email: user.email,
                user_name: user.user_name,
                created_at: user.created_at,
                infra_id: user.infra_id || [],
                role: user.role,
                updated_at: user.updated_at,
                metadata: user.metadata || {},
                invited_by: user.id,
            });

            const refreshTokenRecord = await this.createRefreshToken(user.id, transaction);

            return this.buildAuthResponse(user, refreshTokenRecord.token_id);
        });
    }
}

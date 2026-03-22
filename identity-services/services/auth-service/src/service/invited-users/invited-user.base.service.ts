import { Transaction } from 'sequelize';
import crypto from 'crypto';
import { RefreshToken, UserOTP } from '@/db';
import { signAccessToken, signRefreshToken } from '@/utils/handle-token';
import { generateOTP } from '@/utils/generate-otp';
import { AuthResponse, USER_ROLE, UserData } from '@/types/auth.invited_user.types';

export abstract class BaseService {
    protected async createRefreshToken(userId: string, transaction: Transaction) {
        const expiresAt = new Date();
        expiresAt.setDate(expiresAt.getDate() + 30);

        return RefreshToken.create(
            {
                user_id: userId,
                token_id: crypto.randomUUID(),
                expires_at: expiresAt,
            },
            { transaction },
        );
    }

    protected buildAuthResponse(
        user: {
            id: string;
            email: string;
            user_name: string;
            role: string | USER_ROLE;
            infra_id: string[];
            created_at?: Date;
            profile_url?: string;
        },
        refreshTokenId: string,
    ): AuthResponse {
        return {
            user: {
                id: user.id,
                email: user.email,
                user_name: user.user_name,
                role: user.role as USER_ROLE,
                infra_id: user.infra_id || [],
                createdAt: user.created_at
                    ? user.created_at.toISOString()
                    : new Date().toISOString(),
                profile_url: user.profile_url,
            } as UserData,
            accessToken: signAccessToken({
                sub: user.id,
                email: user.email,
                user_name: user.user_name,
                role: user.role,
            }),
            refreshToken: signRefreshToken({ sub: user.id, tokenId: refreshTokenId }),
            access_token: signAccessToken({
                sub: user.id,
                email: user.email,
                user_name: user.user_name,
                role: user.role,
            }),
            refresh_token: signRefreshToken({ sub: user.id, tokenId: refreshTokenId }),
        };
    }

    protected async createOTP(userId: string, infraId: string, transaction: Transaction) {
        const expiresAt = new Date();
        expiresAt.setMinutes(expiresAt.getMinutes() + 10); // 10 min expiry window
        return UserOTP.create(
            {
                invited_user_id: userId,
                otp: generateOTP(),
                expires_at: expiresAt,
                infra_id: infraId,
            },
            { transaction },
        );
    }
}

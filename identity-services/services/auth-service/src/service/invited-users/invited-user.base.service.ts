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
            roles?: Record<string, string>;
            infra_id: string[];
            created_at?: Date;
            profile_url?: string;
        },
        refreshTokenId: string,
        // The session's original interactive-login time, unix seconds. Omitted (or
        // undefined) means "this call IS the interactive login" — every call site that
        // authenticates the user directly (password, OTP, GitHub OAuth) leaves this unset
        // and gets `now`. InvitedUserAuthService.refresh is the one caller that must pass
        // the value read off the incoming refresh token, never a fresh timestamp.
        authTime?: number,
    ): AuthResponse {
        const auth_time = authTime ?? Math.floor(Date.now() / 1000);
        const tokenClaims = {
            sub: user.id,
            email: user.email,
            user_name: user.user_name,
            role: user.role,
            roles: user.roles,
            auth_time,
        };
        return {
            user: {
                id: user.id,
                email: user.email,
                user_name: user.user_name,
                role: user.role as USER_ROLE,
                roles: user.roles,
                infra_id: user.infra_id || [],
                createdAt: user.created_at
                    ? user.created_at.toISOString()
                    : new Date().toISOString(),
                profile_url: user.profile_url,
            } as UserData,
            accessToken: signAccessToken(tokenClaims),
            refreshToken: signRefreshToken({ sub: user.id, tokenId: refreshTokenId, auth_time }),
            access_token: signAccessToken(tokenClaims),
            refresh_token: signRefreshToken({ sub: user.id, tokenId: refreshTokenId, auth_time }),
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

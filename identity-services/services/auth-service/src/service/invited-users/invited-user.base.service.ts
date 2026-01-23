import { Transaction } from "sequelize";
import crypto from "crypto";
import { InvitedUser, RefreshToken, UserOTP } from "@/db";
import { signAccessToken, signRefreshToken } from "@/utils/handle-token";
import { generateOTP } from "@/utils/generate-otp";

export abstract class BaseService {
    protected async createRefreshToken(userId: string, transaction: Transaction) {
        const expiresAt = new Date();
        expiresAt.setDate(expiresAt.getDate() + 30);

        return RefreshToken.create({
            user_id: userId,
            token_id: crypto.randomUUID(),
            expires_at: expiresAt,
        }, { transaction });
    }

    protected buildAuthResponse(user: InvitedUser, refreshTokenId: string) {
        return {
            user: {
                id: user.id,
                email: user.email,
                user_name: user.user_name,
                infra_id: user.infra_id,
                role: user.role,
                createdAt: user.created_at.toISOString(),
            },
            accessToken: signAccessToken({ sub: user.id, email: user.email }),
            refreshToken: signRefreshToken({ sub: user.id, tokenId: refreshTokenId }),
        };
    }

    protected async createOTP(userId: string, infraId: string, transaction: Transaction) {
        const expiresAt = new Date();
        expiresAt.setMinutes(expiresAt.getMinutes() + 10);
        return UserOTP.create({
            invited_user_id: userId,
            otp: generateOTP(),
            expires_at: expiresAt,
            infra_id: infraId,
        }, { transaction });
    }
}

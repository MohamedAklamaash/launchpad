import jwt, { type Secret, type SignOptions } from "jsonwebtoken";
import { env } from "@/config/env";

const ACCESS_TOKEN: Secret = env.JWT_SECRET;
const REFRESH_TOKEN: Secret = env.JWT_REFRESH_SECRET;
const ACCESS_OPTIONS: SignOptions = {
    expiresIn: env.JWT_EXPIRES_IN as SignOptions["expiresIn"],
};
const REFRESH_OPTIONS: SignOptions = {
    expiresIn: env.JWT_REFRESH_EXPIRES_IN as SignOptions["expiresIn"],
};

export interface RefreshTokenPayload {
    sub: string, // userid
    tokenId: string
}

export interface AccessTokenPayload {
    sub: string, // userid
    email: string
    scope?: string
}

export const signAccessToken = (payload: AccessTokenPayload, expiresIn?: string): string => {
    const options: SignOptions = expiresIn ? { ...ACCESS_OPTIONS, expiresIn: expiresIn as any } : ACCESS_OPTIONS;
    return jwt.sign(payload, ACCESS_TOKEN, options);
}

export const verifyAccessToken = (token: string): AccessTokenPayload => {
    return jwt.verify(token, ACCESS_TOKEN) as AccessTokenPayload;
}

export const signRefreshToken = (payload: RefreshTokenPayload): string => {
    return jwt.sign(payload, REFRESH_TOKEN, REFRESH_OPTIONS);
};

export const verifyRefreshToken = (payload: string): RefreshTokenPayload => {
    return jwt.verify(payload, REFRESH_TOKEN) as RefreshTokenPayload;
};
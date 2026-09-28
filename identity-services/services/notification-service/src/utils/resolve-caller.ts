import { Request } from 'express';
import jwt from 'jsonwebtoken';
import { HttpError } from '@launchpad/common';
import { env } from '@/config/env';

// Mirrors auth-service's AccessTokenPayload. notification-service never mints tokens,
// only verifies them — it shares JWT_SECRET with auth-service so it can do this locally
// instead of calling back over the network for every request.
export interface CallerPayload {
    sub: string;
    email: string;
    user_name: string;
    role: string;
    roles?: Record<string, string>;
}

// Verifies the caller's access token and returns the claims. Never trusts a path, query,
// or body parameter for "who is calling" — that identity comes only from a signature
// the service itself can check.
export const resolveCaller = (req: Request): CallerPayload => {
    const authHeader = req.headers.authorization;
    const token = authHeader?.startsWith('Bearer ')
        ? authHeader.slice('Bearer '.length)
        : undefined;
    if (!token) {
        throw new HttpError(401, 'Authorization header with Bearer token is required');
    }
    try {
        return jwt.verify(token, env.JWT_SECRET) as CallerPayload;
    } catch {
        throw new HttpError(401, 'Invalid or expired access token');
    }
};

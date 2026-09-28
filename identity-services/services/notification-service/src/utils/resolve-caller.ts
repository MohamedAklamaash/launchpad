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
    // Set only on a deliberately narrow-purpose token — currently auth-service's
    // 5-minute password_reset token. Never set on a real session token.
    scope?: string;
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

    let payload: CallerPayload;
    try {
        payload = jwt.verify(token, env.JWT_SECRET) as CallerPayload;
    } catch {
        throw new HttpError(401, 'Invalid or expired access token');
    }

    // A password_reset token proves "this email requested a reset", not "this is an
    // active session" — reject it here the same way auth-service's own
    // verifySessionToken does, rather than letting a leaked reset token double as a
    // notifications-read credential.
    if (payload.scope) {
        throw new HttpError(401, 'This token cannot be used here');
    }

    return payload;
};

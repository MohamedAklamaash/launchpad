import jwt, { type Secret, type SignOptions } from 'jsonwebtoken';
import { env } from '@/config/env';
import { HttpError } from '@launchpad/common';

const ACCESS_TOKEN: Secret = env.JWT_SECRET;
const REFRESH_TOKEN: Secret = env.JWT_REFRESH_SECRET;
const ACCESS_OPTIONS: SignOptions = {
    expiresIn: env.JWT_EXPIRES_IN as SignOptions['expiresIn'],
};
const REFRESH_OPTIONS: SignOptions = {
    expiresIn: env.JWT_REFRESH_EXPIRES_IN as SignOptions['expiresIn'],
};

export interface RefreshTokenPayload {
    sub: string; // userid
    tokenId: string;
    // Unix seconds of the interactive login (password/OTP/GitHub OAuth) that started this
    // session. Set once when the refresh token is first minted and copied unchanged on
    // every rotation in InvitedUserAuthService.refresh — never refreshed to "now". This is
    // what a caller who needs proof-of-recent-login (F6 exit export's reauth gate) must
    // check instead of a token's own `iat`, which a stolen refresh token can mint fresh on
    // every call and would otherwise defeat the whole point of a "recently authenticated"
    // check. Optional in the type only for a narrow-scope token that isn't a session at
    // all (see AccessTokenPayload.auth_time) — every real login/refresh call site sets it.
    auth_time?: number;
}

export interface AccessTokenPayload {
    sub: string; // userid
    email: string;
    user_name: string;
    role: string;
    roles?: Record<string, string>;
    scope?: string;
    // See RefreshTokenPayload.auth_time — carried onto every access token minted from a
    // given login, refreshed or not. Left unset only by PasswordService's short-lived,
    // narrow-scope `password_reset` token, which is not a session and must never satisfy
    // a proof-of-recent-login check even if presented as one — an absent auth_time reads
    // as stale, not exempt, everywhere that checks it.
    auth_time?: number;
}

export const signAccessToken = (payload: AccessTokenPayload, expiresIn?: string): string => {
    const options: SignOptions = expiresIn
        ? { ...ACCESS_OPTIONS, expiresIn: expiresIn as SignOptions['expiresIn'] }
        : ACCESS_OPTIONS;
    return jwt.sign(payload, ACCESS_TOKEN, options);
};

export const verifyAccessToken = (token: string): AccessTokenPayload => {
    try {
        return jwt.verify(token, ACCESS_TOKEN) as AccessTokenPayload;
    } catch {
        throw new HttpError(401, 'Invalid access token');
    }
};

// Every access token carries a `scope` claim, but only ever a truthy one for a
// deliberately narrow-purpose token — currently just PasswordService's 5-minute
// `password_reset` token, which is minted from an email+OTP pair, not a full login, and
// must never be usable as a stand-in for one. Every call site that treats a bearer token
// as "this is an active session" (invite/list/remove-member, update-password, revoke,
// GetCurrentUser) must verify through this instead of the raw verifyAccessToken — a
// scoped token still passes signature and expiry checks, so those alone aren't enough.
export const verifySessionToken = (token: string): AccessTokenPayload => {
    const payload = verifyAccessToken(token);
    if (payload.scope) {
        throw new HttpError(401, 'This token cannot be used as a session credential');
    }
    return payload;
};

export const signRefreshToken = (payload: RefreshTokenPayload): string => {
    return jwt.sign(payload, REFRESH_TOKEN, REFRESH_OPTIONS);
};

export const verifyRefreshToken = (payload: string): RefreshTokenPayload => {
    return jwt.verify(payload, REFRESH_TOKEN) as RefreshTokenPayload;
};

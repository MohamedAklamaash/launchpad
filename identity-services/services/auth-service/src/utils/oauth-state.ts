import crypto from 'crypto';
import type { Request, Response } from 'express';
import { env } from '@/config/env';

// Binds a GitHub OAuth login to the browser that started it (login CSRF / session
// fixation defense). The state is a signed, self-expiring token: `${nonce}.${issuedAt}.${hmac}`.
// It travels twice — once in the `state` query param GitHub echoes back, once in an
// HttpOnly cookie set on the same response — so the callback can require both copies to
// match (double-submit) as well as the signature/expiry to hold.
export const OAUTH_STATE_COOKIE = 'gh_oauth_state';
const STATE_TTL_MS = 10 * 60 * 1000;

// Prefixed so this HMAC can never collide with an HS256 JWT signed with the same
// JWT_SECRET (domain separation) — this state token is not a JWT and must not verify as one.
function sign(payload: string): string {
    return crypto
        .createHmac('sha256', env.JWT_SECRET)
        .update(`oauth-state:${payload}`)
        .digest('base64url');
}

export function generateOAuthState(): string {
    const nonce = crypto.randomBytes(32).toString('base64url'); // 256 bits of entropy
    const issuedAt = Date.now().toString();
    const payload = `${nonce}.${issuedAt}`;
    return `${payload}.${sign(payload)}`;
}

function constantTimeEqual(a: string, b: string): boolean {
    const aBuf = Buffer.from(a);
    const bBuf = Buffer.from(b);
    if (aBuf.length !== bBuf.length) return false;
    return crypto.timingSafeEqual(aBuf, bBuf);
}

function isSignatureValid(state: string): boolean {
    const parts = state.split('.');
    if (parts.length !== 3) return false;
    const [nonce, issuedAt, signature] = parts;
    return constantTimeEqual(signature, sign(`${nonce}.${issuedAt}`));
}

function isUnexpired(state: string): boolean {
    const issuedAt = Number(state.split('.')[1]);
    return Number.isFinite(issuedAt) && Date.now() - issuedAt <= STATE_TTL_MS;
}

// True only when the query-param state and the cookie state are both present, byte-equal,
// correctly signed, and within the TTL. Every check must pass before a token exchange happens.
export function isOAuthStateValid(
    queryState: string | undefined,
    cookieState: string | undefined,
): boolean {
    if (!queryState || !cookieState) return false;
    if (!constantTimeEqual(queryState, cookieState)) return false;
    if (!isSignatureValid(queryState)) return false;
    if (!isUnexpired(queryState)) return false;
    return true;
}

function isHttps(req: Request): boolean {
    return req.secure || req.headers['x-forwarded-proto'] === 'https';
}

export function setOAuthStateCookie(req: Request, res: Response, state: string): void {
    res.cookie(OAUTH_STATE_COOKIE, state, {
        httpOnly: true,
        secure: isHttps(req),
        sameSite: 'lax',
        maxAge: STATE_TTL_MS,
        path: '/',
    });
}

// Clears the cookie unconditionally so a state (and the cookie carrying it) can only ever
// be presented to the callback once, regardless of whether that attempt succeeds.
export function clearOAuthStateCookie(res: Response): void {
    res.clearCookie(OAUTH_STATE_COOKIE, { path: '/' });
}

export function readOAuthStateCookie(req: Request): string | undefined {
    const header = req.headers.cookie;
    if (!header) return undefined;
    for (const part of header.split(';')) {
        const idx = part.indexOf('=');
        if (idx === -1) continue;
        const key = part.slice(0, idx).trim();
        if (key !== OAUTH_STATE_COOKIE) continue;
        try {
            return decodeURIComponent(part.slice(idx + 1).trim());
        } catch {
            return undefined;
        }
    }
    return undefined;
}

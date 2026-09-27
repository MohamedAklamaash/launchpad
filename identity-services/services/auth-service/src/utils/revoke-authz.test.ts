import { test } from 'node:test';
import assert from 'node:assert/strict';

// env.ts validates process.env at import time via zod — every required field needs a
// value before the first (dynamic) import of anything that pulls it in, since these are
// test-only dummy values, never real secrets.
process.env.NODE_ENV = 'test';
process.env.INTERNAL_API_TOKEN = 'x'.repeat(32);
process.env.DATABASE_USER_NAME = 'test';
process.env.DATABASE_PASSWORD = 'test';
process.env.DATABASE_HOST = 'localhost';
process.env.DATABASE_NAME = 'test';
process.env.AUTH_DB_URL = 'postgres://test:test@localhost:5432/test';
process.env.JWT_SECRET = 'x'.repeat(32);
process.env.JWT_REFRESH_SECRET = 'y'.repeat(32);
process.env.JWT_EXPIRES_IN = '15m';
process.env.JWT_REFRESH_EXPIRES_IN = '7d';
process.env.GATEWAY_URL = 'http://localhost:8000';
process.env.GITHUB_TOKEN = 'ghp_test';
process.env.GITHUB_CLIENT_ID = 'test';
process.env.GITHUB_CLIENT_SECRET = 'test';
process.env.REDIS_HOST = 'localhost';
process.env.REDIS_PASSWORD = 'test';
process.env.RABBITMQ_URL = 'amqp://guest:guest@localhost:5672/';

const { signAccessToken, signRefreshToken } = await import('@/utils/handle-token');
const { resolveRevokeCallerId } = await import('@/utils/revoke-authz');
const { HttpError } = await import('@launchpad/common');

const accessTokenFor = (sub: string) =>
    signAccessToken({ sub, email: `${sub}@example.com`, user_name: sub, role: 'user' });

test('no Authorization header and no refresh token is unauthenticated', () => {
    assert.throws(
        () => resolveRevokeCallerId(undefined, undefined),
        (error: unknown) => error instanceof HttpError && error.statusCode === 401,
    );
});

test('a malformed Authorization header (no token part) is rejected', () => {
    assert.throws(
        () => resolveRevokeCallerId('Bearer', undefined),
        (error: unknown) => error instanceof HttpError && error.statusCode === 401,
    );
});

test('an invalid access token with no refresh token to fall back on is rejected', () => {
    assert.throws(
        () => resolveRevokeCallerId('Bearer not-a-real-token', undefined),
        (error: unknown) => error instanceof HttpError && error.statusCode === 401,
    );
});

test('an invalid access token falls through to a valid refresh token', () => {
    // The dashboard sends both credentials together on the reauth_required path; either
    // one proving the same caller is enough, so a broken access token must not shadow a
    // still-valid refresh token.
    const refreshToken = signRefreshToken({ sub: 'user-a', tokenId: 'tok-1' });
    const userId = resolveRevokeCallerId('Bearer not-a-real-token', refreshToken);
    assert.equal(userId, 'user-a');
});

test('a refresh token signed with the access-token secret is rejected (no cross-token confusion)', () => {
    // Same shape of claim (`sub`), wrong secret — proves the two token types cannot be
    // swapped in to satisfy each other's check.
    const tokenSignedAsAccess = accessTokenFor('user-a');
    assert.throws(
        () => resolveRevokeCallerId(undefined, tokenSignedAsAccess),
        (error: unknown) => error instanceof HttpError && error.statusCode === 401,
    );
});

test('a valid access token resolves to its own subject', () => {
    const userId = resolveRevokeCallerId(`Bearer ${accessTokenFor('user-a')}`, undefined);
    assert.equal(userId, 'user-a');
});

test('a valid refresh token resolves to its own subject when no Authorization header is present', () => {
    const refreshToken = signRefreshToken({ sub: 'user-b', tokenId: 'tok-1' });
    const userId = resolveRevokeCallerId(undefined, refreshToken);
    assert.equal(userId, 'user-b');
});

test('the Authorization header takes precedence over a refresh token for a different user', () => {
    // Mirrors the real caller: an access token for the logged-in user plus whatever
    // refresh token happens to be sitting in local storage. The resolved identity must
    // always be the access token's, never the body's.
    const refreshTokenForSomeoneElse = signRefreshToken({ sub: 'user-c', tokenId: 'tok-2' });
    const userId = resolveRevokeCallerId(
        `Bearer ${accessTokenFor('user-a')}`,
        refreshTokenForSomeoneElse,
    );
    assert.equal(userId, 'user-a');
});

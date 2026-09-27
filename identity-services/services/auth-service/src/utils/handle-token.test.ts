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

const { signAccessToken, verifyAccessToken, signRefreshToken, verifyRefreshToken } =
    await import('@/utils/handle-token');

const BASE_CLAIMS = { sub: 'user-1', email: 'u@example.com', user_name: 'u', role: 'user' };

test('a fresh access token carries the auth_time it was signed with', () => {
    const authTime = Math.floor(Date.now() / 1000) - 5;
    const token = signAccessToken({ ...BASE_CLAIMS, auth_time: authTime });
    const payload = verifyAccessToken(token);
    assert.equal(payload.auth_time, authTime);
});

test('a refresh token carries the auth_time it was signed with, independent of its own iat', () => {
    const authTime = Math.floor(Date.now() / 1000) - 1000;
    const token = signRefreshToken({ sub: 'user-1', tokenId: 'tok-1', auth_time: authTime });
    const payload = verifyRefreshToken(token) as unknown as { auth_time: number; iat: number };
    assert.equal(payload.auth_time, authTime);
    // The refresh token's own iat is "now" (it was just signed) even though auth_time
    // points at a login from a while ago — this is exactly the distinction _reauth_ok on
    // the infrastructure-service side depends on.
    assert.ok(payload.iat > authTime);
});

test('re-signing an access token at refresh time with the original auth_time never advances it', () => {
    // Mirrors InvitedUserAuthService.refresh: it reads auth_time off the verified refresh
    // token and passes it straight into a brand-new access token, rather than stamping a
    // fresh value the way an interactive login does.
    const originalLogin = Math.floor(Date.now() / 1000) - 3600; // an hour ago
    const refreshToken = signRefreshToken({
        sub: 'user-1',
        tokenId: 'tok-1',
        auth_time: originalLogin,
    });
    const { auth_time: authTimeFromRefreshToken } = verifyRefreshToken(refreshToken) as {
        auth_time: number;
    };

    const newAccessToken = signAccessToken({ ...BASE_CLAIMS, auth_time: authTimeFromRefreshToken });
    const newPayload = verifyAccessToken(newAccessToken);

    assert.equal(newPayload.auth_time, originalLogin);
    assert.notEqual(newPayload.auth_time, Math.floor(Date.now() / 1000));
});

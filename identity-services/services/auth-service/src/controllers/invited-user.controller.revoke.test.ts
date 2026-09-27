import { test, mock } from 'node:test';
import assert from 'node:assert/strict';
import type { Request, Response } from 'express';

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

// Importing the controller pulls in InvitedUserFacade -> InvitedUserService, which
// constructs a BullMQ Queue and an AMQP publisher at module load. Neither is awaited or
// exercised by these tests (the facade method itself is mocked below), but the process
// they open handles for is why the auth-service `test` script runs with
// --test-force-exit — otherwise this file alone keeps the runner alive after every test
// has already passed.
const { signAccessToken, signRefreshToken } = await import('@/utils/handle-token');
const { InvitedUserFacade } = await import('@/service/invited-user.facade.service');
const { RevokeRefreshToken } = await import('@/controllers/invited-user.controller');
const { HttpError } = await import('@launchpad/common');

const accessTokenFor = (sub: string) =>
    signAccessToken({ sub, email: `${sub}@example.com`, user_name: sub, role: 'user' });

const fakeRequest = (authorization: string | undefined, body: Record<string, unknown>) =>
    ({ headers: { authorization }, body }) as unknown as Request;

const fakeResponse = () => {
    let statusCode: number | undefined;
    const res = {
        status(code: number) {
            statusCode = code;
            return res;
        },
        send() {
            return res;
        },
    } as unknown as Response;
    return { res, getStatus: () => statusCode };
};

test('unauthenticated request is rejected with 401 before touching the facade', async () => {
    const revokeSpy = mock.method(
        InvitedUserFacade.prototype,
        'revokeRefreshToken',
        async () => true,
    );
    try {
        const { res } = fakeResponse();
        await assert.rejects(
            () => RevokeRefreshToken(fakeRequest(undefined, {}), res),
            (error: unknown) => error instanceof HttpError && error.statusCode === 401,
        );
        assert.equal(revokeSpy.mock.callCount(), 0);
    } finally {
        revokeSpy.mock.restore();
    }
});

test("a token for user A cannot revoke user B's sessions", async () => {
    let revokedUserId: string | undefined;
    const revokeSpy = mock.method(
        InvitedUserFacade.prototype,
        'revokeRefreshToken',
        async (userId: string) => {
            revokedUserId = userId;
            return true;
        },
    );
    try {
        const { res, getStatus } = fakeResponse();
        // Body claims user-b, but the caller only holds user-a's access token — there is
        // no code path that reads a target user out of the body.
        await RevokeRefreshToken(
            fakeRequest(`Bearer ${accessTokenFor('user-a')}`, { userId: 'user-b' }),
            res,
        );
        assert.equal(getStatus(), 204);
        assert.equal(revokedUserId, 'user-a');
        assert.notEqual(revokedUserId, 'user-b');
    } finally {
        revokeSpy.mock.restore();
    }
});

test('a caller can revoke their own sessions', async () => {
    let revokedUserId: string | undefined;
    const revokeSpy = mock.method(
        InvitedUserFacade.prototype,
        'revokeRefreshToken',
        async (userId: string) => {
            revokedUserId = userId;
            return true;
        },
    );
    try {
        const { res, getStatus } = fakeResponse();
        await RevokeRefreshToken(fakeRequest(`Bearer ${accessTokenFor('user-a')}`, {}), res);
        assert.equal(getStatus(), 204);
        assert.equal(revokedUserId, 'user-a');
        assert.equal(revokeSpy.mock.callCount(), 1);
    } finally {
        revokeSpy.mock.restore();
    }
});

test('a caller can revoke their own sessions with only a refresh token', async () => {
    let revokedUserId: string | undefined;
    const revokeSpy = mock.method(
        InvitedUserFacade.prototype,
        'revokeRefreshToken',
        async (userId: string) => {
            revokedUserId = userId;
            return true;
        },
    );
    try {
        const { res, getStatus } = fakeResponse();
        const refreshToken = signRefreshToken({ sub: 'user-a', tokenId: 'tok-1' });
        await RevokeRefreshToken(fakeRequest(undefined, { refreshToken }), res);
        assert.equal(getStatus(), 204);
        assert.equal(revokedUserId, 'user-a');
    } finally {
        revokeSpy.mock.restore();
    }
});

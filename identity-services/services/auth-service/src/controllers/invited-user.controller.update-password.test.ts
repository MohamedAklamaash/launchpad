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

// See invited-user.controller.revoke.test.ts for why --test-force-exit is required —
// importing the controller opens BullMQ/AMQP handles this file never awaits.
const { signAccessToken } = await import('@/utils/handle-token');
const { InvitedUserFacade } = await import('@/service/invited-user.facade.service');
const { UpdatePassword } = await import('@/controllers/invited-user.controller');
const { HttpError } = await import('@launchpad/common');

const accessTokenFor = (sub: string) =>
    signAccessToken({ sub, email: `${sub}@example.com`, user_name: sub, role: 'user' });

const passwordResetTokenFor = (sub: string) =>
    signAccessToken(
        { sub, email: `${sub}@example.com`, user_name: sub, role: 'user', scope: 'password_reset' },
        '5m',
    );

const fakeRequest = (authorization: string | undefined, body: Record<string, unknown>) =>
    ({ headers: { authorization }, body }) as unknown as Request;

const fakeResponse = () => {
    let statusCode: number | undefined;
    let body: unknown;
    const res = {
        status(code: number) {
            statusCode = code;
            return res;
        },
        json(payload: unknown) {
            body = payload;
            return res;
        },
    } as unknown as Response;
    return { res, getStatus: () => statusCode, getBody: () => body };
};

test('unauthenticated request is rejected with 401 before touching the facade', async () => {
    const updateSpy = mock.method(InvitedUserFacade.prototype, 'updatePassword', async () => true);
    try {
        const { res } = fakeResponse();
        await assert.rejects(
            () =>
                UpdatePassword(
                    fakeRequest(undefined, {
                        oldPassword: 'old-secret',
                        newPassword: 'new-secret',
                    }),
                    res,
                ),
            (error: unknown) => error instanceof HttpError && error.statusCode === 401,
        );
        assert.equal(updateSpy.mock.callCount(), 0);
    } finally {
        updateSpy.mock.restore();
    }
});

test('a body-supplied email cannot redirect the update to another account', async () => {
    let targetUserId: string | undefined;
    const updateSpy = mock.method(
        InvitedUserFacade.prototype,
        'updatePassword',
        async (input: { user_id: string }) => {
            targetUserId = input.user_id;
            return true;
        },
    );
    try {
        const { res, getStatus } = fakeResponse();
        // Body claims another user's email; the caller only holds user-a's access token
        // and there is no code path that reads a target user out of the body.
        await UpdatePassword(
            fakeRequest(`Bearer ${accessTokenFor('user-a')}`, {
                email: 'user-b@example.com',
                oldPassword: 'old-secret',
                newPassword: 'new-secret',
            }),
            res,
        );
        assert.equal(getStatus(), 200);
        assert.equal(targetUserId, 'user-a');
        assert.notEqual(targetUserId, 'user-b');
    } finally {
        updateSpy.mock.restore();
    }
});

test('a password_reset token cannot be used to update a password', async () => {
    const updateSpy = mock.method(InvitedUserFacade.prototype, 'updatePassword', async () => true);
    try {
        const { res } = fakeResponse();
        await assert.rejects(
            () =>
                UpdatePassword(
                    fakeRequest(`Bearer ${passwordResetTokenFor('user-a')}`, {
                        oldPassword: 'old-secret',
                        newPassword: 'new-secret',
                    }),
                    res,
                ),
            (error: unknown) => error instanceof HttpError && error.statusCode === 401,
        );
        assert.equal(updateSpy.mock.callCount(), 0);
    } finally {
        updateSpy.mock.restore();
    }
});

test('a caller can update their own password', async () => {
    let targetUserId: string | undefined;
    const updateSpy = mock.method(
        InvitedUserFacade.prototype,
        'updatePassword',
        async (input: { user_id: string }) => {
            targetUserId = input.user_id;
            return true;
        },
    );
    try {
        const { res, getStatus, getBody } = fakeResponse();
        await UpdatePassword(
            fakeRequest(`Bearer ${accessTokenFor('user-a')}`, {
                oldPassword: 'old-secret',
                newPassword: 'new-secret',
            }),
            res,
        );
        assert.equal(getStatus(), 200);
        assert.deepEqual(getBody(), { success: true });
        assert.equal(targetUserId, 'user-a');
        assert.equal(updateSpy.mock.callCount(), 1);
    } finally {
        updateSpy.mock.restore();
    }
});

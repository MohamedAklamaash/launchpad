import { test, mock } from 'node:test';
import assert from 'node:assert/strict';
import type { NextFunction, Request, Response } from 'express';

// env.ts validates process.env at import time via zod — dummy values only, never real
// secrets, and must be set before the first import of anything that pulls it in.
process.env.NODE_ENV = 'test';
process.env.INTERNAL_API_TOKEN = 'x'.repeat(32);
process.env.MAIL_USER = 'test@example.com';
process.env.MAIL_APP_PASSWORD = 'test';
process.env.FROM_MAIL = 'test@example.com';
process.env.REDIS_HOST = 'localhost';
process.env.REDIS_PASSWORD = 'test';
process.env.MONGODB_URL = 'mongodb://localhost:27017/test';
process.env.JWT_SECRET = 'x'.repeat(32);
process.env.AUTH_SERVICE_URL = 'http://localhost:3000';
process.env.GATEWAY_SERVICE_URL = 'http://localhost:8000';

const jwt = (await import('jsonwebtoken')).default;
const { HttpError } = await import('@launchpad/common');
const { notificationService } = await import('@/service/notification.service');
const { GetMyNotifications } = await import('@/controllers/notification.controller');

const signToken = (sub: string) =>
    jwt.sign(
        { sub, email: `${sub}@example.com`, user_name: sub, role: 'user' },
        process.env.JWT_SECRET as string,
    );

const fakeRequest = (authorization: string | undefined) =>
    ({ headers: { authorization } }) as unknown as Request;

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

const fakeNext = () => {
    let captured: unknown;
    const next = ((err?: unknown) => {
        captured = err;
    }) as NextFunction;
    return { next, getError: () => captured };
};

test('GetMyNotifications: unauthenticated request is rejected with 401 before touching the service', async () => {
    const getSpy = mock.method(notificationService, 'getByUser', async () => []);
    try {
        const { res } = fakeResponse();
        const { next, getError } = fakeNext();
        await GetMyNotifications(fakeRequest(undefined), res, next);
        const error = getError();
        assert.ok(error instanceof HttpError && error.statusCode === 401);
        assert.equal(getSpy.mock.callCount(), 0);
    } finally {
        getSpy.mock.restore();
    }
});

test("GetMyNotifications: only the caller's own notifications are fetched — there is no target-user parameter", async () => {
    let queriedUserId: string | undefined;
    const getSpy = mock.method(notificationService, 'getByUser', async (userId: string) => {
        queriedUserId = userId;
        return [{ user_id: userId, user_name: userId, email: `${userId}@example.com` }];
    });
    try {
        const { res, getStatus, getBody } = fakeResponse();
        const { next, getError } = fakeNext();
        await GetMyNotifications(fakeRequest(`Bearer ${signToken('user-a')}`), res, next);
        assert.equal(getError(), undefined);
        assert.equal(getStatus(), 200);
        assert.equal(queriedUserId, 'user-a');
        assert.equal((getBody() as Array<{ user_id: string }>)[0].user_id, 'user-a');
    } finally {
        getSpy.mock.restore();
    }
});

test('GetMyNotifications: a service failure is forwarded as a 500 via next', async () => {
    const getSpy = mock.method(notificationService, 'getByUser', async () => {
        throw new Error('mongo down');
    });
    try {
        const { res } = fakeResponse();
        const { next, getError } = fakeNext();
        await GetMyNotifications(fakeRequest(`Bearer ${signToken('user-a')}`), res, next);
        const error = getError();
        assert.ok(error instanceof HttpError && error.statusCode === 500);
    } finally {
        getSpy.mock.restore();
    }
});

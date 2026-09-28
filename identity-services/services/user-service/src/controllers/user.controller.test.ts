import { test, mock } from 'node:test';
import assert from 'node:assert/strict';
import type { NextFunction, Request, Response } from 'express';

// env.ts validates process.env at import time via zod — dummy values only, never real
// secrets, and must be set before the first import of anything that pulls it in.
process.env.NODE_ENV = 'test';
process.env.INTERNAL_API_TOKEN = 'x'.repeat(32);
process.env.USER_DB_URL = 'mysql://test:test@localhost:3306/test';
process.env.RABBITMQ_URL = 'amqp://guest:guest@localhost:5672/';
process.env.JWT_SECRET = 'x'.repeat(32);

const jwt = (await import('jsonwebtoken')).default;
const { HttpError } = await import('@launchpad/common');
const { userService } = await import('@/service/user.service');
const { GetUserById, SearchUsers } = await import('@/controllers/user.controller');
import type { User } from '@/types/user.type';

const signToken = (sub: string) =>
    jwt.sign(
        { sub, email: `${sub}@example.com`, user_name: sub, role: 'user' },
        process.env.JWT_SECRET as string,
    );

const signPasswordResetToken = (sub: string) =>
    jwt.sign(
        { sub, email: `${sub}@example.com`, user_name: sub, role: 'user', scope: 'password_reset' },
        process.env.JWT_SECRET as string,
        { expiresIn: '5m' },
    );

const makeUser = (overrides: Partial<User>): User => ({
    user_id: 'user-a',
    user_name: 'user-a',
    role: 'user',
    email: 'user-a@example.com',
    infra_id: ['infra-1'],
    created_at: new Date(),
    updated_at: new Date(),
    ...overrides,
});

const fakeRequest = (
    authorization: string | undefined,
    params: Record<string, string> = {},
    query: Record<string, string> = {},
) => ({ headers: { authorization }, params, query }) as unknown as Request;

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

test('GetUserById: unauthenticated request is rejected with 401 before touching the service', async () => {
    const getSpy = mock.method(userService, 'getUserById', async () => makeUser({}));
    try {
        const { res } = fakeResponse();
        const { next, getError } = fakeNext();
        await GetUserById(fakeRequest(undefined, { userId: 'user-a' }), res, next);
        const error = getError();
        assert.ok(error instanceof HttpError && error.statusCode === 401);
        assert.equal(getSpy.mock.callCount(), 0);
    } finally {
        getSpy.mock.restore();
    }
});

test("GetUserById: a token for user A cannot fetch user B's profile", async () => {
    const getSpy = mock.method(userService, 'getUserById', async () =>
        makeUser({ user_id: 'user-b' }),
    );
    try {
        const { res } = fakeResponse();
        const { next, getError } = fakeNext();
        await GetUserById(
            fakeRequest(`Bearer ${signToken('user-a')}`, { userId: 'user-b' }),
            res,
            next,
        );
        const error = getError();
        assert.ok(error instanceof HttpError && error.statusCode === 403);
        assert.equal(getSpy.mock.callCount(), 0);
    } finally {
        getSpy.mock.restore();
    }
});

test('GetUserById: a caller can fetch their own profile', async () => {
    const getSpy = mock.method(userService, 'getUserById', async (id: string) =>
        makeUser({ user_id: id }),
    );
    try {
        const { res, getStatus, getBody } = fakeResponse();
        const { next, getError } = fakeNext();
        await GetUserById(
            fakeRequest(`Bearer ${signToken('user-a')}`, { userId: 'user-a' }),
            res,
            next,
        );
        assert.equal(getError(), undefined);
        assert.equal(getStatus(), 200);
        assert.equal((getBody() as User).user_id, 'user-a');
        assert.equal(getSpy.mock.callCount(), 1);
    } finally {
        getSpy.mock.restore();
    }
});

test('GetUserById: a password_reset token cannot be used to read a profile', async () => {
    const getSpy = mock.method(userService, 'getUserById', async () => makeUser({}));
    try {
        const { res } = fakeResponse();
        const { next, getError } = fakeNext();
        await GetUserById(
            fakeRequest(`Bearer ${signPasswordResetToken('user-a')}`, { userId: 'user-a' }),
            res,
            next,
        );
        const error = getError();
        assert.ok(error instanceof HttpError && error.statusCode === 401);
        assert.equal(getSpy.mock.callCount(), 0);
    } finally {
        getSpy.mock.restore();
    }
});

test('SearchUsers: a password_reset token cannot be used to search', async () => {
    const { res } = fakeResponse();
    const { next, getError } = fakeNext();
    await SearchUsers(
        fakeRequest(`Bearer ${signPasswordResetToken('user-a')}`, {}, { q: 'john' }),
        res,
        next,
    );
    const error = getError();
    assert.ok(error instanceof HttpError && error.statusCode === 401);
});

test('SearchUsers: unauthenticated request is rejected with 401', async () => {
    const { res } = fakeResponse();
    const { next, getError } = fakeNext();
    await SearchUsers(fakeRequest(undefined, {}, { q: 'john' }), res, next);
    const error = getError();
    assert.ok(error instanceof HttpError && error.statusCode === 401);
});

test('SearchUsers: a query shorter than the minimum length is rejected', async () => {
    const { res } = fakeResponse();
    const { next, getError } = fakeNext();
    await SearchUsers(fakeRequest(`Bearer ${signToken('user-a')}`, {}, { q: 'jo' }), res, next);
    const error = getError();
    assert.ok(error instanceof HttpError && error.statusCode === 400);
});

test('SearchUsers: results are scoped to users who share an infra with the caller', async () => {
    const getByIdSpy = mock.method(userService, 'getUserById', async () =>
        makeUser({ user_id: 'user-a', infra_id: ['infra-1'] }),
    );
    const searchSpy = mock.method(userService, 'searchUsers', async () => [
        makeUser({ user_id: 'user-b', user_name: 'john-b', infra_id: ['infra-1'] }),
        makeUser({ user_id: 'user-c', user_name: 'john-c', infra_id: ['infra-2'] }),
    ]);
    try {
        const { res, getStatus, getBody } = fakeResponse();
        const { next, getError } = fakeNext();
        await SearchUsers(
            fakeRequest(`Bearer ${signToken('user-a')}`, {}, { q: 'john' }),
            res,
            next,
        );
        assert.equal(getError(), undefined);
        assert.equal(getStatus(), 200);
        const body = getBody() as Array<Record<string, unknown>>;
        assert.equal(body.length, 1);
        assert.equal(body[0].user_id, 'user-b');
        // Minimal fields only — no infra_id, role, invited_by, or metadata.
        assert.deepEqual(
            Object.keys(body[0]).sort(),
            ['email', 'profile_url', 'user_id', 'user_name'].sort(),
        );
    } finally {
        getByIdSpy.mock.restore();
        searchSpy.mock.restore();
    }
});

test('SearchUsers: an unknown caller record yields an empty result rather than an error', async () => {
    const getByIdSpy = mock.method(userService, 'getUserById', async () => {
        throw new HttpError(404, 'User not found');
    });
    const searchSpy = mock.method(userService, 'searchUsers', async () => [makeUser({})]);
    try {
        const { res, getStatus, getBody } = fakeResponse();
        const { next, getError } = fakeNext();
        await SearchUsers(
            fakeRequest(`Bearer ${signToken('ghost')}`, {}, { q: 'john' }),
            res,
            next,
        );
        assert.equal(getError(), undefined);
        assert.equal(getStatus(), 200);
        assert.deepEqual(getBody(), []);
        assert.equal(searchSpy.mock.callCount(), 0);
    } finally {
        getByIdSpy.mock.restore();
        searchSpy.mock.restore();
    }
});

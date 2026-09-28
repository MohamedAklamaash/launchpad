import { test, mock } from 'node:test';
import assert from 'node:assert/strict';
import type { Request, Response } from 'express';

// env.ts validates process.env at import time via zod — dummy values only, never real
// secrets, and must be set before the first (dynamic) import of anything that pulls it in.
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

const { InvitedUserFacade } = await import('@/service/invited-user.facade.service');
const { ForgotPassword } = await import('@/controllers/invited-user.controller');

const fakeRequest = (body: Record<string, unknown>) => ({ body }) as unknown as Request;

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

test('never returns the OTP, for a known email', async () => {
    const forgotSpy = mock.method(
        InvitedUserFacade.prototype,
        'forgotPassword',
        async () => undefined,
    );
    try {
        const { res, getStatus, getBody } = fakeResponse();
        await ForgotPassword(fakeRequest({ email: 'known@example.com' }), res);
        assert.equal(getStatus(), 202);
        const body = getBody() as Record<string, unknown>;
        assert.equal('otp' in body, false);
        assert.equal(typeof body.message, 'string');
    } finally {
        forgotSpy.mock.restore();
    }
});

test('an unknown email gets the exact same response as a known one', async () => {
    const forgotSpy = mock.method(
        InvitedUserFacade.prototype,
        'forgotPassword',
        async () => undefined,
    );
    try {
        const known = fakeResponse();
        const unknown = fakeResponse();

        await ForgotPassword(fakeRequest({ email: 'known@example.com' }), known.res);
        await ForgotPassword(fakeRequest({ email: 'never-registered@example.com' }), unknown.res);

        assert.equal(known.getStatus(), unknown.getStatus());
        assert.deepEqual(known.getBody(), unknown.getBody());
    } finally {
        forgotSpy.mock.restore();
    }
});

test('a facade rejection (unknown email, DB error, anything) never surfaces to the caller', async () => {
    const forgotSpy = mock.method(InvitedUserFacade.prototype, 'forgotPassword', async () => {
        throw new Error('boom');
    });
    const originalConsoleError = console.error;
    console.error = () => {};
    try {
        const { res, getStatus, getBody } = fakeResponse();
        await ForgotPassword(fakeRequest({ email: 'anything@example.com' }), res);
        assert.equal(getStatus(), 202);
        assert.equal('otp' in (getBody() as object), false);
    } finally {
        console.error = originalConsoleError;
        forgotSpy.mock.restore();
    }
});

test('the response does not wait for the background password-reset work to finish', async () => {
    let resolveFacadeCall: (() => void) | undefined;
    const forgotSpy = mock.method(
        InvitedUserFacade.prototype,
        'forgotPassword',
        () =>
            new Promise<void>((resolve) => {
                resolveFacadeCall = resolve;
            }),
    );
    try {
        const { res, getStatus } = fakeResponse();
        await ForgotPassword(fakeRequest({ email: 'slow@example.com' }), res);
        // The controller already responded even though the facade call it kicked off is
        // still pending — proves this isn't awaited, so response timing can't leak
        // whether `email` triggered real work.
        assert.equal(getStatus(), 202);
        assert.equal(typeof resolveFacadeCall, 'function');
    } finally {
        resolveFacadeCall?.();
        forgotSpy.mock.restore();
    }
});

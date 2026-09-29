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
process.env.GITHUB_CLIENT_ID = 'test-client-id';
process.env.GITHUB_CLIENT_SECRET = 'test';
process.env.REDIS_HOST = 'localhost';
process.env.REDIS_PASSWORD = 'test';
process.env.RABBITMQ_URL = 'amqp://guest:guest@localhost:5672/';

// Importing the controller pulls in UserFacadeService, whose methods we mock below rather
// than exercise for real (no GitHub, no DB).
const { LoginWithGitHub, GitHubCallback } = await import('@/controllers/user.controller');
const { UserFacadeService } = await import('@/service/user.facade.service');
const { OAUTH_STATE_COOKIE, generateOAuthState } = await import('@/utils/oauth-state');
const { githubHttpClient } = await import('@/utils/http-client');

type Jar = { value?: string };

const fakeRequest = (query: Record<string, string>, jar: Jar) =>
    ({
        query,
        secure: false,
        headers: {
            cookie: jar.value
                ? `${OAUTH_STATE_COOKIE}=${encodeURIComponent(jar.value)}`
                : undefined,
        },
    }) as unknown as Request;

const fakeResponse = (jar: Jar) => {
    let statusCode: number | undefined;
    let jsonBody: unknown;
    let redirectUrl: string | undefined;
    const res = {
        cookie(name: string, value: string) {
            if (name === OAUTH_STATE_COOKIE) jar.value = value;
            return res;
        },
        clearCookie(name: string) {
            if (name === OAUTH_STATE_COOKIE) jar.value = undefined;
            return res;
        },
        status(code: number) {
            statusCode = code;
            return res;
        },
        json(body: unknown) {
            jsonBody = body;
            return res;
        },
        redirect(url: string) {
            redirectUrl = url;
            return res;
        },
    } as unknown as Response;
    return {
        res,
        getStatus: () => statusCode,
        getJson: () => jsonBody,
        getRedirect: () => redirectUrl,
    };
};

const stateFromRedirectUrl = (url: string) => new URL(url).searchParams.get('state');

test('the GitHub authorize URL includes a state bound to a cookie on the same response', async () => {
    const jar: Jar = {};
    const { res, getRedirect } = fakeResponse(jar);
    await LoginWithGitHub(fakeRequest({}, jar), res);

    const redirectUrl = getRedirect();
    assert.ok(redirectUrl?.startsWith('https://github.com/login/oauth/authorize?'));
    const state = stateFromRedirectUrl(redirectUrl!);
    assert.ok(state && state.length > 0);
    assert.equal(
        jar.value,
        state,
        'the state cookie must carry the same value as the authorize URL',
    );
});

test('callback with no state and no cookie at all is rejected before any token exchange', async () => {
    const exchangeSpy = mock.method(githubHttpClient, 'post', async () => {
        throw new Error('token exchange should not have been called');
    });
    try {
        const jar: Jar = {};
        const { res, getStatus } = fakeResponse(jar);
        await GitHubCallback(fakeRequest({ code: 'some-code' }, jar), res);

        assert.equal(getStatus(), 400);
        assert.equal(exchangeSpy.mock.callCount(), 0);
    } finally {
        exchangeSpy.mock.restore();
    }
});

test('callback with a valid state cookie but no state query param (attacker link omits it) is rejected before any token exchange', async () => {
    const exchangeSpy = mock.method(githubHttpClient, 'post', async () => {
        throw new Error('token exchange should not have been called');
    });
    try {
        // Simulates the realistic login-CSRF attempt: the victim has a fresh, legitimate
        // state cookie from their own /login, but the attacker's crafted callback link
        // carries no state (or someone else's), since the attacker never gets that cookie.
        const jar: Jar = { value: generateOAuthState() };
        const { res, getStatus } = fakeResponse(jar);
        await GitHubCallback(fakeRequest({ code: 'attacker-code' }, jar), res);

        assert.equal(getStatus(), 400);
        assert.equal(exchangeSpy.mock.callCount(), 0);
    } finally {
        exchangeSpy.mock.restore();
    }
});

test('callback whose state does not match the cookie is rejected before any token exchange', async () => {
    const exchangeSpy = mock.method(githubHttpClient, 'post', async () => {
        throw new Error('token exchange should not have been called');
    });
    try {
        const jar: Jar = { value: generateOAuthState() };
        const { res, getStatus, getJson } = fakeResponse(jar);
        await GitHubCallback(
            fakeRequest({ code: 'some-code', state: generateOAuthState() }, jar),
            res,
        );

        assert.equal(getStatus(), 400);
        assert.match((getJson() as { message: string }).message, /state/i);
        assert.equal(exchangeSpy.mock.callCount(), 0);
    } finally {
        exchangeSpy.mock.restore();
    }
});

test('callback with an expired state is rejected before any token exchange', async () => {
    const exchangeSpy = mock.method(githubHttpClient, 'post', async () => {
        throw new Error('token exchange should not have been called');
    });
    const elevenMinutesAgo = Date.now() - 11 * 60 * 1000;
    const nowSpy = mock.method(Date, 'now', () => elevenMinutesAgo);
    const expiredState = generateOAuthState();
    nowSpy.mock.restore();

    try {
        const jar: Jar = { value: expiredState };
        const { res, getStatus } = fakeResponse(jar);
        await GitHubCallback(fakeRequest({ code: 'some-code', state: expiredState }, jar), res);

        assert.equal(getStatus(), 400);
        assert.equal(exchangeSpy.mock.callCount(), 0);
    } finally {
        exchangeSpy.mock.restore();
    }
});

test('a state cannot be replayed after its first (successful) use', async () => {
    const handleCallbackSpy = mock.method(
        UserFacadeService.prototype,
        'handleCallback',
        async () => ({
            token: 'gh-token',
            username: 'octocat',
            github_id: '1',
            avatar_url: 'https://example.com/a.png',
            email: 'octocat@example.com',
        }),
    );
    const upsertUserSpy = mock.method(UserFacadeService.prototype, 'upsertUser', async () => ({
        accessToken: 'access-1',
        refreshToken: 'refresh-1',
    }));

    try {
        const jar: Jar = {};
        const loginRes = fakeResponse(jar);
        await LoginWithGitHub(fakeRequest({}, jar), loginRes.res);
        const state = stateFromRedirectUrl(loginRes.getRedirect()!)!;

        const first = fakeResponse(jar);
        await GitHubCallback(fakeRequest({ code: 'valid-code', state }, jar), first.res);
        assert.equal(first.getStatus(), undefined, 'first use should not be rejected');
        assert.equal(handleCallbackSpy.mock.callCount(), 1);
        const redirectUrl = first.getRedirect();
        assert.ok(redirectUrl?.includes('#access_token=access-1&refresh_token=refresh-1'));
        assert.ok(
            !redirectUrl?.includes('?access_token='),
            'tokens must not be in the query string',
        );
        assert.equal(jar.value, undefined, 'the cookie must be cleared after first use');

        const replay = fakeResponse(jar);
        await GitHubCallback(fakeRequest({ code: 'valid-code', state }, jar), replay.res);
        assert.equal(replay.getStatus(), 400);
        assert.equal(
            handleCallbackSpy.mock.callCount(),
            1,
            'a replayed state must not trigger a second exchange',
        );
    } finally {
        handleCallbackSpy.mock.restore();
        upsertUserSpy.mock.restore();
    }
});

test('a valid, matching, unexpired state proceeds to token exchange', async () => {
    const handleCallbackSpy = mock.method(
        UserFacadeService.prototype,
        'handleCallback',
        async () => ({
            token: 'gh-token',
            username: 'octocat',
            github_id: '1',
            avatar_url: 'https://example.com/a.png',
            email: 'octocat@example.com',
        }),
    );
    const upsertUserSpy = mock.method(UserFacadeService.prototype, 'upsertUser', async () => ({
        accessToken: 'access-2',
        refreshToken: 'refresh-2',
    }));

    try {
        const jar: Jar = {};
        const loginRes = fakeResponse(jar);
        await LoginWithGitHub(fakeRequest({}, jar), loginRes.res);
        const state = stateFromRedirectUrl(loginRes.getRedirect()!)!;

        const callbackRes = fakeResponse(jar);
        await GitHubCallback(fakeRequest({ code: 'valid-code', state }, jar), callbackRes.res);

        assert.equal(handleCallbackSpy.mock.callCount(), 1);
        assert.equal(upsertUserSpy.mock.callCount(), 1);
        assert.equal(callbackRes.getStatus(), undefined);
        assert.ok(callbackRes.getRedirect()?.startsWith('http://localhost:3000/auth/callback#'));
    } finally {
        handleCallbackSpy.mock.restore();
        upsertUserSpy.mock.restore();
    }
});

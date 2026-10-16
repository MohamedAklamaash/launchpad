import { test, mock } from 'node:test';
import assert from 'node:assert/strict';

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
process.env.ORGS_ENABLED = 'true';

const { sequelize } = await import('@/db/sequalize');
const { User, InvitedUser, Membership, RefreshToken } = await import('@/db');
const { SwitchOrgService } = await import('@/service/organizations/switch-org.service');
const { signRefreshToken, verifyAccessToken } = await import('@/utils/handle-token');
const { HttpError } = await import('@launchpad/common');

const CALLER_ID = 'caller-1';
const ACCOUNT_ID = 'account-1';
const TARGET_ORG_ID = 'org-target';

const withTransactionMock = () =>
    mock.method(sequelize, 'transaction', async (cb: (t: object) => unknown) => cb({}));

const service = new SwitchOrgService();

const stubUserLookup = () => {
    const userFindSpy = mock.method(User, 'findByPk', async () => null);
    const invitedFindSpy = mock.method(InvitedUser, 'findByPk', async () => ({
        id: CALLER_ID,
        email: 'a@example.com',
        user_name: 'a',
        role: 'developer',
        roles: {},
        infra_id: [],
        account_id: ACCOUNT_ID,
        created_at: new Date(),
    }));
    return { userFindSpy, invitedFindSpy };
};

test('switch-org mints a new token scoped to the target org for an active member, and rotates the refresh token', async () => {
    const txSpy = withTransactionMock();
    const { userFindSpy, invitedFindSpy } = stubUserLookup();
    const destroySpy = mock.fn(async () => undefined);
    const findTokenSpy = mock.method(RefreshToken, 'findOne', async () => ({
        destroy: destroySpy,
    }));
    const findMembershipSpy = mock.method(Membership, 'findOne', async () => ({
        organization_id: TARGET_ORG_ID,
        account_id: ACCOUNT_ID,
        role: 'developer',
        removed_at: null,
    }));
    let createdAttrs: Record<string, unknown> = {};
    const createTokenSpy = mock.method(
        RefreshToken,
        'create',
        async (attrs: Record<string, unknown>) => {
            createdAttrs = attrs;
            return { ...attrs, token_id: 'tok-new' };
        },
    );

    const refreshTokenValue = signRefreshToken({ sub: CALLER_ID, tokenId: 'tok-old' });

    try {
        const result = await service.switchOrg({
            callerId: CALLER_ID,
            refreshToken: refreshTokenValue,
            organizationId: TARGET_ORG_ID,
        });

        assert.equal(destroySpy.mock.callCount(), 1);
        assert.equal(createdAttrs.active_org_id, TARGET_ORG_ID);
        const payload = verifyAccessToken(result.accessToken);
        assert.equal(payload.org_id, TARGET_ORG_ID);
        assert.equal(payload.account_id, ACCOUNT_ID);
    } finally {
        txSpy.mock.restore();
        userFindSpy.mock.restore();
        invitedFindSpy.mock.restore();
        findTokenSpy.mock.restore();
        findMembershipSpy.mock.restore();
        createTokenSpy.mock.restore();
    }
});

test('switch-org rejects a target org the caller is not an active member of (404, not 403)', async () => {
    const txSpy = withTransactionMock();
    const { userFindSpy, invitedFindSpy } = stubUserLookup();
    const findTokenSpy = mock.method(RefreshToken, 'findOne', async () => ({
        destroy: mock.fn(async () => undefined),
    }));
    const findMembershipSpy = mock.method(Membership, 'findOne', async () => null);

    const refreshTokenValue = signRefreshToken({ sub: CALLER_ID, tokenId: 'tok-old' });

    try {
        await assert.rejects(
            () =>
                service.switchOrg({
                    callerId: CALLER_ID,
                    refreshToken: refreshTokenValue,
                    organizationId: 'org-not-a-member',
                }),
            (error: unknown) => error instanceof HttpError && error.statusCode === 404,
        );
        // Membership.findOne must only ever be asked about an ACTIVE row — the query
        // itself excludes a tombstone, never a post-hoc filter in application code.
        const [{ where }] = findMembershipSpy.mock.calls[0]!.arguments as [{ where: object }];
        assert.deepEqual(where, {
            organization_id: 'org-not-a-member',
            account_id: ACCOUNT_ID,
            removed_at: null,
        });
    } finally {
        txSpy.mock.restore();
        userFindSpy.mock.restore();
        invitedFindSpy.mock.restore();
        findTokenSpy.mock.restore();
        findMembershipSpy.mock.restore();
    }
});

test('switch-org rejects a removed (tombstoned) member the same way as a non-member', async () => {
    const txSpy = withTransactionMock();
    const { userFindSpy, invitedFindSpy } = stubUserLookup();
    const findTokenSpy = mock.method(RefreshToken, 'findOne', async () => ({
        destroy: mock.fn(async () => undefined),
    }));
    // The query filters removed_at: null, so a tombstoned row is never returned by the
    // DB in the first place — this simulates exactly that (findOne returns null).
    const findMembershipSpy = mock.method(Membership, 'findOne', async () => null);

    const refreshTokenValue = signRefreshToken({ sub: CALLER_ID, tokenId: 'tok-old' });

    try {
        await assert.rejects(
            () =>
                service.switchOrg({
                    callerId: CALLER_ID,
                    refreshToken: refreshTokenValue,
                    organizationId: TARGET_ORG_ID,
                }),
            (error: unknown) => error instanceof HttpError && error.statusCode === 404,
        );
    } finally {
        txSpy.mock.restore();
        userFindSpy.mock.restore();
        invitedFindSpy.mock.restore();
        findTokenSpy.mock.restore();
        findMembershipSpy.mock.restore();
    }
});

test('switch-org rejects when the refresh token belongs to a different caller than the one asking', async () => {
    const refreshTokenValue = signRefreshToken({ sub: 'someone-else', tokenId: 'tok-old' });

    await assert.rejects(
        () =>
            service.switchOrg({
                callerId: CALLER_ID,
                refreshToken: refreshTokenValue,
                organizationId: TARGET_ORG_ID,
            }),
        (error: unknown) => error instanceof HttpError && error.statusCode === 401,
    );
});

test('switch-org rejects outright while ORGS_ENABLED is off', async () => {
    const original = process.env.ORGS_ENABLED;
    process.env.ORGS_ENABLED = 'false';
    const { env } = await import('@/config/env');
    // env is validated/frozen at import time (zod), so this test flips the live object's
    // field rather than re-importing the module — matching the flag-off test pattern used
    // across the organizations/*.test.ts suite's env checks.
    (env as { ORGS_ENABLED: boolean }).ORGS_ENABLED = false;

    const refreshTokenValue = signRefreshToken({ sub: CALLER_ID, tokenId: 'tok-old' });

    try {
        await assert.rejects(
            () =>
                service.switchOrg({
                    callerId: CALLER_ID,
                    refreshToken: refreshTokenValue,
                    organizationId: TARGET_ORG_ID,
                }),
            HttpError,
        );
    } finally {
        (env as { ORGS_ENABLED: boolean }).ORGS_ENABLED = true;
        process.env.ORGS_ENABLED = original;
    }
});

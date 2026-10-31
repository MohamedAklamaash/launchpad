import { test, mock } from 'node:test';
import assert from 'node:assert/strict';

process.env.NODE_ENV = 'test';
process.env.INTERNAL_API_TOKEN = 'x'.repeat(32);
process.env.DATABASE_USER_NAME = 'test';
process.env.DATABASE_PASSWORD = 'test';
process.env.DATABASE_HOST = '127.0.0.1';
process.env.DATABASE_PORT = '1';
process.env.DATABASE_NAME = 'test';
process.env.AUTH_DB_URL = 'postgres://test:test@127.0.0.1:1/test';
process.env.JWT_SECRET = 'x'.repeat(32);
process.env.JWT_REFRESH_SECRET = 'y'.repeat(32);
process.env.JWT_EXPIRES_IN = '15m';
process.env.JWT_REFRESH_EXPIRES_IN = '7d';
process.env.GATEWAY_URL = 'http://localhost:8000';
process.env.GITHUB_TOKEN = 'ghp_test';
process.env.GITHUB_CLIENT_ID = 'test';
process.env.GITHUB_CLIENT_SECRET = 'test';
process.env.REDIS_HOST = '127.0.0.1';
process.env.REDIS_PORT = '1';
process.env.REDIS_PASSWORD = 'test';
process.env.RABBITMQ_URL = 'amqp://guest:guest@127.0.0.1:1/';
process.env.ORGS_ENABLED = 'true';

const { sequelize } = await import('@/db/sequalize');
const { User, InvitedUser, Membership, RefreshToken } = await import('@/db');
const { SwitchOrgService } = await import('@/service/organizations/switch-org.service');
const { signRefreshToken, verifyAccessToken } = await import('@/utils/handle-token');
const { HttpError } = await import('@launchpad/common');

// Security-review follow-up (L1): switch-org now canonicalizes `organization_id` as a
// UUID before doing anything else with it, so every org id used in these tests must be
// well-formed — a bare slug like the previous `'org-target'` fixtures used would now
// 400 before ever reaching the behavior under test.
const CALLER_ID = '11111111-1111-1111-1111-111111111111';
const ACCOUNT_ID = '22222222-2222-2222-2222-222222222222';
const TARGET_ORG_ID = '33333333-3333-3333-3333-333333333333';
const OTHER_ORG_ID = '44444444-4444-4444-4444-444444444444';

// switchOrg locks the RefreshToken row for the transaction's lifetime (security review
// M2) — the mocked transaction object needs a LOCK.UPDATE token the same way
// membership.service.test.ts's withLockingTransactionMock does.
const withTransactionMock = () =>
    mock.method(sequelize, 'transaction', async (cb: (t: object) => unknown) =>
        cb({ LOCK: { UPDATE: 'UPDATE' } }),
    );

const service = new SwitchOrgService();

const stubUserLookup = (overrides: { emailVerifiedAt?: Date | null } = {}) => {
    // `??` would treat an explicitly-passed `null` the same as "not provided" — use
    // `in` so a test asserting the unverified case (emailVerifiedAt: null) actually
    // gets null, not silently falls back to verified.
    const emailVerifiedAt = 'emailVerifiedAt' in overrides ? overrides.emailVerifiedAt : new Date();
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
        email_verified_at: emailVerifiedAt,
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

test('switch-org canonicalizes a differently-cased organization_id before using it as a lookup/scope key', async () => {
    const txSpy = withTransactionMock();
    const { userFindSpy, invitedFindSpy } = stubUserLookup();
    const findTokenSpy = mock.method(RefreshToken, 'findOne', async () => ({
        destroy: mock.fn(async () => undefined),
    }));
    const findMembershipSpy = mock.method(Membership, 'findOne', async () => ({
        organization_id: TARGET_ORG_ID,
        account_id: ACCOUNT_ID,
        role: 'developer',
        removed_at: null,
    }));
    const createTokenSpy = mock.method(RefreshToken, 'create', async (attrs: object) => ({
        ...attrs,
        token_id: 'tok-new',
    }));

    const refreshTokenValue = signRefreshToken({ sub: CALLER_ID, tokenId: 'tok-old' });

    try {
        const result = await service.switchOrg({
            callerId: CALLER_ID,
            refreshToken: refreshTokenValue,
            organizationId: TARGET_ORG_ID.toUpperCase(),
        });

        const [{ where }] = findMembershipSpy.mock.calls[0]!.arguments as [
            { where: { organization_id: string } },
        ];
        assert.equal(where.organization_id, TARGET_ORG_ID);
        const payload = verifyAccessToken(result.accessToken);
        assert.equal(payload.org_id, TARGET_ORG_ID);
    } finally {
        txSpy.mock.restore();
        userFindSpy.mock.restore();
        invitedFindSpy.mock.restore();
        findTokenSpy.mock.restore();
        findMembershipSpy.mock.restore();
        createTokenSpy.mock.restore();
    }
});

test('switch-org rejects a malformed organization_id with 400, not 500 or a raw exception', async () => {
    await assert.rejects(
        () =>
            service.switchOrg({
                callerId: CALLER_ID,
                refreshToken: signRefreshToken({ sub: CALLER_ID, tokenId: 'tok-old' }),
                organizationId: 'not-a-uuid',
            }),
        (error: unknown) => error instanceof HttpError && error.statusCode === 400,
    );
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
                    organizationId: OTHER_ORG_ID,
                }),
            (error: unknown) => error instanceof HttpError && error.statusCode === 404,
        );
        // Membership.findOne must only ever be asked about an ACTIVE row — the query
        // itself excludes a tombstone, never a post-hoc filter in application code.
        const [{ where }] = findMembershipSpy.mock.calls[0]!.arguments as [{ where: object }];
        assert.deepEqual(where, {
            organization_id: OTHER_ORG_ID,
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

// Security review H1(b): even if an unverified InvitedUser row's account_id happens to
// be set (e.g. a pre-fix backfill-era email collision, or the row somehow being linked
// through some other path) and that account genuinely has an active Membership, an
// unverified identity must never be authorized into it.
test('H1(b): switch-org never authorizes an unverified InvitedUser into a Membership, even one its account_id points at', async () => {
    const txSpy = withTransactionMock();
    const { userFindSpy, invitedFindSpy } = stubUserLookup({ emailVerifiedAt: null });
    const findTokenSpy = mock.method(RefreshToken, 'findOne', async () => ({
        destroy: mock.fn(async () => undefined),
    }));
    const findMembershipSpy = mock.method(Membership, 'findOne', async () => ({
        organization_id: TARGET_ORG_ID,
        account_id: ACCOUNT_ID,
        role: 'owner',
        removed_at: null,
    }));

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
        // The Membership table must never even be queried with this account's real
        // account_id — an unverified identity's account_id is treated as absent.
        assert.equal(findMembershipSpy.mock.callCount(), 0);
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

// Security review L2: an invalid/expired refresh token must 401, not fall through to a
// generic 500 — verifyRefreshToken (handle-token.ts) now wraps jwt.verify itself, which
// fixes this at the one shared primitive both switch-org and /auth/refresh call.
test('switch-org rejects an unparseable refresh token with 401, not an uncaught exception', async () => {
    await assert.rejects(
        () =>
            service.switchOrg({
                callerId: CALLER_ID,
                refreshToken: 'not-a-jwt-at-all',
                organizationId: TARGET_ORG_ID,
            }),
        (error: unknown) => error instanceof HttpError && error.statusCode === 401,
    );
});

// Security review M2: two concurrent switch-org calls against the same refresh token
// must leave exactly one live chain. Row locking is a real-Postgres property this
// mocked suite can't exercise directly, so this proves the application-level half of
// the invariant instead: once the first call's transaction has destroyed the row, a
// second call that only now gets to look it up must see nothing and 401 — never destroy
// (or mint from) a row that's already gone, and never silently succeed twice.
test('M2: a second switch-org call against an already-rotated refresh token 401s instead of minting a second chain', async () => {
    const txSpy = withTransactionMock();
    const { userFindSpy, invitedFindSpy } = stubUserLookup();
    let tokenStillLive = true;
    const destroySpy = mock.fn(async () => {
        tokenStillLive = false;
    });
    const findTokenSpy = mock.method(RefreshToken, 'findOne', async () =>
        tokenStillLive ? { destroy: destroySpy } : null,
    );
    const findMembershipSpy = mock.method(Membership, 'findOne', async () => ({
        organization_id: TARGET_ORG_ID,
        account_id: ACCOUNT_ID,
        role: 'developer',
        removed_at: null,
    }));
    const createTokenSpy = mock.method(RefreshToken, 'create', async (attrs: object) => ({
        ...attrs,
        token_id: 'tok-new',
    }));

    const refreshTokenValue = signRefreshToken({ sub: CALLER_ID, tokenId: 'tok-old' });
    const input = {
        callerId: CALLER_ID,
        refreshToken: refreshTokenValue,
        organizationId: TARGET_ORG_ID,
    };

    try {
        const first = await service.switchOrg(input);
        assert.ok(first.accessToken);

        await assert.rejects(
            () => service.switchOrg(input),
            (error: unknown) => error instanceof HttpError && error.statusCode === 401,
        );
        assert.equal(destroySpy.mock.callCount(), 1);
    } finally {
        txSpy.mock.restore();
        userFindSpy.mock.restore();
        invitedFindSpy.mock.restore();
        findTokenSpy.mock.restore();
        findMembershipSpy.mock.restore();
        createTokenSpy.mock.restore();
    }
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

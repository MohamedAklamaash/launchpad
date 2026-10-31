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
const { Account, InvitedUser, Membership, User, UserOTP, RefreshToken } = await import('@/db');
const { InvitedUserAuthService } =
    await import('@/service/invited-users/invited-user.auth.service');
const { verifyAccessToken } = await import('@/utils/handle-token');
const { personalOrgId } = await import('@/utils/org-id');
const { accountIdForEmail } = await import('@/utils/account-id');
const { hashPassword } = await import('@/utils/handle-password');
const { otpAttempts } = await import('@/utils/otp-attempts');

const USER_ID = '019f2c2b-9b19-71a9-8f3e-cd81d13effb9';
const ACCOUNT_ID = '9762168d-fbdc-5ee1-aa27-39ad30b6223f';
const PERSONAL_ORG_ID = personalOrgId(USER_ID);

// refresh() locks the RefreshToken row for the transaction's lifetime (security review
// M2) — the mocked transaction object needs a LOCK.UPDATE token.
const withTransactionMock = () =>
    mock.method(sequelize, 'transaction', async (cb: (t: object) => unknown) =>
        cb({ LOCK: { UPDATE: 'UPDATE' } }),
    );

const service = new InvitedUserAuthService();

test('login (ORGS_ENABLED=true): the minted access token carries org_id (the personal org) and account_id', async () => {
    const txSpy = withTransactionMock();
    const passwordHash = await hashPassword('secret123');
    const findUserSpy = mock.method(InvitedUser, 'findOne', async () => ({
        id: USER_ID,
        email: 'a@example.com',
        user_name: 'a',
        role: 'user',
        roles: {},
        infra_id: [],
        account_id: ACCOUNT_ID,
        password_hash: passwordHash,
        created_at: new Date(),
        email_verified_at: new Date(),
    }));
    const findOtpSpy = mock.method(UserOTP, 'findOne', async () => null);
    const refreshSpy = mock.method(RefreshToken, 'create', async (attrs: object) => ({
        ...attrs,
        token_id: 'tok-1',
    }));

    try {
        const result = await service.login({ email: 'a@example.com', password: 'secret123' });
        const payload = verifyAccessToken(result.accessToken);
        assert.equal(payload.org_id, PERSONAL_ORG_ID);
        assert.equal(payload.account_id, ACCOUNT_ID);
    } finally {
        txSpy.mock.restore();
        findUserSpy.mock.restore();
        findOtpSpy.mock.restore();
        refreshSpy.mock.restore();
    }
});

test('login (ORGS_ENABLED=true): account_id is omitted (not sent as null) when the row has no linked Account', async () => {
    const txSpy = withTransactionMock();
    const passwordHash = await hashPassword('secret123');
    const findUserSpy = mock.method(InvitedUser, 'findOne', async () => ({
        id: USER_ID,
        email: 'a@example.com',
        user_name: 'a',
        role: 'user',
        roles: {},
        infra_id: [],
        account_id: null,
        password_hash: passwordHash,
        created_at: new Date(),
        email_verified_at: new Date(),
    }));
    const findOtpSpy = mock.method(UserOTP, 'findOne', async () => null);
    const refreshSpy = mock.method(RefreshToken, 'create', async (attrs: object) => ({
        ...attrs,
        token_id: 'tok-1',
    }));

    try {
        const result = await service.login({ email: 'a@example.com', password: 'secret123' });
        const payload = verifyAccessToken(result.accessToken);
        assert.equal(payload.org_id, PERSONAL_ORG_ID);
        assert.equal('account_id' in payload, false);
    } finally {
        txSpy.mock.restore();
        findUserSpy.mock.restore();
        findOtpSpy.mock.restore();
        refreshSpy.mock.restore();
    }
});

// Security review H1(c): the verify hook itself links the newly-verified InvitedUser
// row to an Account, so the very first token it ever mints with org_id/account_id is
// already correct — no separate batch backfill required.
test('H1(c): authenticateWithOTP links account_id at verify time when the claim has none', async () => {
    const txSpy = withTransactionMock();
    const claim = {
        userId: USER_ID,
        email: 'newly-verified@example.com',
        user_name: 'newly-verified',
        role: 'user',
        roles: {},
        infra_id: [],
        created_at: new Date(),
        attempts: 1,
        accountId: null,
    };
    const claimSpy = mock.method(otpAttempts, 'claimAttempt', async () => claim);
    const resetSpy = mock.method(otpAttempts, 'resetAttempts', async () => undefined);
    const findOtpSpy = mock.method(UserOTP, 'findOne', async () => ({
        destroy: async () => undefined,
    }));
    const verifiedAt = new Date();
    const querySpy = mock.method(sequelize, 'query', async () => [
        { email_verified_at: verifiedAt },
    ]);
    // linkAccountForVerifiedIdentity's collision check — no other row holds this
    // account id yet.
    const userFindOneSpy = mock.method(User, 'findOne', async () => null);
    const invitedFindOneSpy = mock.method(InvitedUser, 'findOne', async () => null);
    const findOrCreateSpy = mock.method(Account, 'findOrCreate', async () => [{}, true]);
    const updateSpy = mock.method(InvitedUser, 'update', async () => [1]);
    const refreshSpy = mock.method(RefreshToken, 'create', async (attrs: object) => ({
        ...attrs,
        token_id: 'tok-1',
    }));

    try {
        const result = await service.authenticateWithOTP({
            email: claim.email,
            otp: '123456',
        });

        const expectedAccountId = accountIdForEmail(claim.email);
        const payload = verifyAccessToken(result.accessToken);
        assert.equal(payload.account_id, expectedAccountId);
        assert.equal(payload.org_id, PERSONAL_ORG_ID);

        // Account.findOrCreate used the deterministic formula, and InvitedUser.update
        // linked it guarded by account_id: null (never overwriting an existing link).
        const findOrCreateArgs = findOrCreateSpy.mock.calls[0]!.arguments[0] as {
            where: { id: string };
        };
        assert.equal(findOrCreateArgs.where.id, expectedAccountId);
        assert.deepEqual(updateSpy.mock.calls[0]!.arguments[0], { account_id: expectedAccountId });
        assert.deepEqual((updateSpy.mock.calls[0]!.arguments[1] as { where: object }).where, {
            id: USER_ID,
            account_id: null,
        });
    } finally {
        txSpy.mock.restore();
        claimSpy.mock.restore();
        resetSpy.mock.restore();
        findOtpSpy.mock.restore();
        querySpy.mock.restore();
        userFindOneSpy.mock.restore();
        invitedFindOneSpy.mock.restore();
        findOrCreateSpy.mock.restore();
        updateSpy.mock.restore();
        refreshSpy.mock.restore();
    }
});

// Security review R2 (third bullet): the verify hook must NOT merge into an Account a
// different credential row already holds — that merge is the backfill's own reviewed
// decision, not a live-request one.
test('R2: authenticateWithOTP declines to link when the Account is already held by a different row', async () => {
    const txSpy = withTransactionMock();
    const claim = {
        userId: USER_ID,
        email: 'collides-with-someone@example.com',
        user_name: 'newly-verified',
        role: 'user',
        roles: {},
        infra_id: [],
        created_at: new Date(),
        attempts: 1,
        accountId: null,
    };
    const claimSpy = mock.method(otpAttempts, 'claimAttempt', async () => claim);
    const resetSpy = mock.method(otpAttempts, 'resetAttempts', async () => undefined);
    const findOtpSpy = mock.method(UserOTP, 'findOne', async () => ({
        destroy: async () => undefined,
    }));
    const verifiedAt = new Date();
    const querySpy = mock.method(sequelize, 'query', async () => [
        { email_verified_at: verifiedAt },
    ]);
    // A different InvitedUser row already holds this account id.
    const userFindOneSpy = mock.method(User, 'findOne', async () => null);
    const invitedFindOneSpy = mock.method(
        InvitedUser,
        'findOne',
        async () => ({ id: 'some-other-row' }) as unknown,
    );
    const findOrCreateSpy = mock.method(Account, 'findOrCreate', async () => {
        throw new Error('must not create/touch the Account when declining to link');
    });
    const updateSpy = mock.method(InvitedUser, 'update', async () => {
        throw new Error('must not link account_id when declining');
    });
    const refreshSpy = mock.method(RefreshToken, 'create', async (attrs: object) => ({
        ...attrs,
        token_id: 'tok-1',
    }));

    try {
        const result = await service.authenticateWithOTP({
            email: claim.email,
            otp: '123456',
        });

        const payload = verifyAccessToken(result.accessToken);
        assert.equal('account_id' in payload, false);
        assert.equal(payload.org_id, PERSONAL_ORG_ID);
    } finally {
        txSpy.mock.restore();
        claimSpy.mock.restore();
        resetSpy.mock.restore();
        findOtpSpy.mock.restore();
        querySpy.mock.restore();
        userFindOneSpy.mock.restore();
        invitedFindOneSpy.mock.restore();
        findOrCreateSpy.mock.restore();
        updateSpy.mock.restore();
        refreshSpy.mock.restore();
    }
});

test('refresh preserves an existing active_org_id instead of resetting to the personal org, when an active Membership backs it', async () => {
    const txSpy = withTransactionMock();
    const SWITCHED_ORG_ID = '11111111-1111-1111-1111-111111111111';
    const destroySpy = mock.fn(async () => undefined);
    const findTokenSpy = mock.method(RefreshToken, 'findOne', async () => ({
        active_org_id: SWITCHED_ORG_ID,
        destroy: destroySpy,
    }));
    const findUserSpy = mock.method(InvitedUser, 'findByPk', async () => ({
        id: USER_ID,
        email: 'a@example.com',
        user_name: 'a',
        role: 'user',
        roles: {},
        infra_id: [],
        account_id: ACCOUNT_ID,
        created_at: new Date(),
    }));
    // Security review H3(a): a non-personal active_org_id must be re-validated against
    // the canonical Membership table on every refresh — an active row here is what
    // allows it to survive.
    const findMembershipSpy = mock.method(Membership, 'findOne', async () => ({
        organization_id: SWITCHED_ORG_ID,
        account_id: ACCOUNT_ID,
        role: 'developer',
        removed_at: null,
    }));
    let createdAttrs: Record<string, unknown> = {};
    const createSpy = mock.method(
        RefreshToken,
        'create',
        async (attrs: Record<string, unknown>) => {
            createdAttrs = attrs;
            return { ...attrs, token_id: 'tok-2' };
        },
    );

    try {
        const { signRefreshToken } = await import('@/utils/handle-token');
        const refreshTokenValue = signRefreshToken({ sub: USER_ID, tokenId: 'tok-1' });
        const result = await service.refresh(refreshTokenValue);

        assert.equal(createdAttrs.active_org_id, SWITCHED_ORG_ID);
        const payload = verifyAccessToken(result.accessToken);
        assert.equal(payload.org_id, SWITCHED_ORG_ID);
        assert.equal(destroySpy.mock.callCount(), 1);
    } finally {
        txSpy.mock.restore();
        findTokenSpy.mock.restore();
        findUserSpy.mock.restore();
        findMembershipSpy.mock.restore();
        createSpy.mock.restore();
    }
});

// Security review H3(a): the actual attack this closes. A member is removed from an
// org (tombstoned) while they're still holding a refresh token scoped to it — without
// re-validation, they could keep refreshing a token minting access to that org for up
// to the refresh token's own 30-day lifetime, far past the org_role cache's 60s TTL.
test('H3(a): refresh resets active_org_id to the personal org once the backing Membership is gone (removed member)', async () => {
    const txSpy = withTransactionMock();
    const REMOVED_ORG_ID = '11111111-1111-1111-1111-111111111111';
    const destroySpy = mock.fn(async () => undefined);
    const findTokenSpy = mock.method(RefreshToken, 'findOne', async () => ({
        active_org_id: REMOVED_ORG_ID,
        destroy: destroySpy,
    }));
    const findUserSpy = mock.method(InvitedUser, 'findByPk', async () => ({
        id: USER_ID,
        email: 'a@example.com',
        user_name: 'a',
        role: 'user',
        roles: {},
        infra_id: [],
        account_id: ACCOUNT_ID,
        created_at: new Date(),
    }));
    // The query filters removed_at: null, so a tombstoned membership is never returned —
    // this simulates exactly that.
    const findMembershipSpy = mock.method(Membership, 'findOne', async () => null);
    let createdAttrs: Record<string, unknown> = {};
    const createSpy = mock.method(
        RefreshToken,
        'create',
        async (attrs: Record<string, unknown>) => {
            createdAttrs = attrs;
            return { ...attrs, token_id: 'tok-2' };
        },
    );

    try {
        const { signRefreshToken } = await import('@/utils/handle-token');
        const refreshTokenValue = signRefreshToken({ sub: USER_ID, tokenId: 'tok-1' });
        const result = await service.refresh(refreshTokenValue);

        assert.equal(createdAttrs.active_org_id, PERSONAL_ORG_ID);
        const payload = verifyAccessToken(result.accessToken);
        assert.equal(payload.org_id, PERSONAL_ORG_ID);
        assert.notEqual(payload.org_id, REMOVED_ORG_ID);
    } finally {
        txSpy.mock.restore();
        findTokenSpy.mock.restore();
        findUserSpy.mock.restore();
        findMembershipSpy.mock.restore();
        createSpy.mock.restore();
    }
});

// Security review M2: the refresh-token analog of switch-org.service.test.ts's
// concurrency test — see that file's comment for what a mocked suite can and can't
// prove about real Postgres row locking.
test('M2: a second refresh against an already-rotated token 401s instead of minting a second chain', async () => {
    const txSpy = withTransactionMock();
    let tokenStillLive = true;
    const destroySpy = mock.fn(async () => {
        tokenStillLive = false;
    });
    const findTokenSpy = mock.method(RefreshToken, 'findOne', async () =>
        tokenStillLive ? { active_org_id: null, destroy: destroySpy } : null,
    );
    const findUserSpy = mock.method(InvitedUser, 'findByPk', async () => ({
        id: USER_ID,
        email: 'a@example.com',
        user_name: 'a',
        role: 'user',
        roles: {},
        infra_id: [],
        account_id: ACCOUNT_ID,
        created_at: new Date(),
    }));
    const createSpy = mock.method(RefreshToken, 'create', async (attrs: object) => ({
        ...attrs,
        token_id: 'tok-2',
    }));

    try {
        const { signRefreshToken } = await import('@/utils/handle-token');
        const { HttpError } = await import('@launchpad/common');
        const refreshTokenValue = signRefreshToken({ sub: USER_ID, tokenId: 'tok-1' });

        const first = await service.refresh(refreshTokenValue);
        assert.ok(first.accessToken);

        await assert.rejects(
            () => service.refresh(refreshTokenValue),
            (error: unknown) => error instanceof HttpError && error.statusCode === 401,
        );
        assert.equal(destroySpy.mock.callCount(), 1);
    } finally {
        txSpy.mock.restore();
        findTokenSpy.mock.restore();
        findUserSpy.mock.restore();
        createSpy.mock.restore();
    }
});

test('refresh backfills active_org_id to the personal org exactly once for a pre-ticket-5 refresh token', async () => {
    const txSpy = withTransactionMock();
    const destroySpy = mock.fn(async () => undefined);
    const findTokenSpy = mock.method(RefreshToken, 'findOne', async () => ({
        active_org_id: null, // pre-ticket-5 row
        destroy: destroySpy,
    }));
    const findUserSpy = mock.method(InvitedUser, 'findByPk', async () => ({
        id: USER_ID,
        email: 'a@example.com',
        user_name: 'a',
        role: 'user',
        roles: {},
        infra_id: [],
        account_id: ACCOUNT_ID,
        created_at: new Date(),
    }));
    let createdAttrs: Record<string, unknown> = {};
    const createSpy = mock.method(
        RefreshToken,
        'create',
        async (attrs: Record<string, unknown>) => {
            createdAttrs = attrs;
            return { ...attrs, token_id: 'tok-2' };
        },
    );

    try {
        const { signRefreshToken } = await import('@/utils/handle-token');
        const refreshTokenValue = signRefreshToken({ sub: USER_ID, tokenId: 'tok-1' });
        const result = await service.refresh(refreshTokenValue);

        assert.equal(createdAttrs.active_org_id, PERSONAL_ORG_ID);
        const payload = verifyAccessToken(result.accessToken);
        assert.equal(payload.org_id, PERSONAL_ORG_ID);
    } finally {
        txSpy.mock.restore();
        findTokenSpy.mock.restore();
        findUserSpy.mock.restore();
        createSpy.mock.restore();
    }
});

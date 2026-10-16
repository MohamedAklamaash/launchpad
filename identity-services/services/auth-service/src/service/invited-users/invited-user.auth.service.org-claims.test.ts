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
const { InvitedUser, UserOTP, RefreshToken } = await import('@/db');
const { InvitedUserAuthService } =
    await import('@/service/invited-users/invited-user.auth.service');
const { verifyAccessToken } = await import('@/utils/handle-token');
const { personalOrgId } = await import('@/utils/org-id');
const { hashPassword } = await import('@/utils/handle-password');

const USER_ID = '019f2c2b-9b19-71a9-8f3e-cd81d13effb9';
const ACCOUNT_ID = '9762168d-fbdc-5ee1-aa27-39ad30b6223f';
const PERSONAL_ORG_ID = personalOrgId(USER_ID);

const withTransactionMock = () =>
    mock.method(sequelize, 'transaction', async (cb: (t: object) => unknown) => cb({}));

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

test('refresh preserves an existing active_org_id instead of resetting to the personal org', async () => {
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

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
// Deliberately left unset (default false, scope item 4) — tokens/refresh must be
// byte-for-byte unchanged, including tolerating a non-UUID legacy id, which a real
// deployment never has but this suite's fixtures (and plenty of pre-F9 tests) do.

const { sequelize } = await import('@/db/sequalize');
const { InvitedUser, RefreshToken } = await import('@/db');
const { InvitedUserAuthService } =
    await import('@/service/invited-users/invited-user.auth.service');
const { verifyAccessToken, signRefreshToken } = await import('@/utils/handle-token');

// refresh() locks the RefreshToken row for the transaction's lifetime (security review
// M2, unconditional on ORGS_ENABLED) — the mocked transaction object needs a
// LOCK.UPDATE token.
const withTransactionMock = () =>
    mock.method(sequelize, 'transaction', async (cb: (t: object) => unknown) =>
        cb({ LOCK: { UPDATE: 'UPDATE' } }),
    );

const service = new InvitedUserAuthService();

test('flag off: a minted access token carries no org_id or account_id claim, even for a non-UUID legacy id', async () => {
    const txSpy = withTransactionMock();
    const destroySpy = mock.fn(async () => undefined);
    const findTokenSpy = mock.method(RefreshToken, 'findOne', async () => ({
        active_org_id: null,
        destroy: destroySpy,
    }));
    const findUserSpy = mock.method(InvitedUser, 'findByPk', async () => ({
        id: 'legacy-non-uuid-id',
        email: 'a@example.com',
        user_name: 'a',
        role: 'user',
        roles: {},
        infra_id: [],
        account_id: 'also-not-a-uuid',
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
        const refreshTokenValue = signRefreshToken({ sub: 'legacy-non-uuid-id', tokenId: 'tok-1' });
        const result = await service.refresh(refreshTokenValue);

        // No UUID validation ever ran against the non-UUID ids above — proof that
        // createRefreshToken/refresh never call personalOrgId while the flag is off.
        assert.equal(createdAttrs.active_org_id, null);

        const payload = verifyAccessToken(result.accessToken);
        assert.equal('org_id' in payload, false);
        assert.equal('account_id' in payload, false);
    } finally {
        txSpy.mock.restore();
        findTokenSpy.mock.restore();
        findUserSpy.mock.restore();
        createSpy.mock.restore();
    }
});

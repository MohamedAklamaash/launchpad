import { test, mock, beforeEach } from 'node:test';
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
const { InvitedUser, RefreshToken, User } = await import('@/db');
const { UserFacadeService } = await import('@/service/user.facade.service');
const { personalOrgId } = await import('@/utils/org-id');
const { installPersonalOrgStore } = await import('@/test-support/personal-org-store');

const store = await installPersonalOrgStore();
const service = new UserFacadeService();

const USER_ID = '44444444-4444-4444-8444-444444444444';
const ACCOUNT_ID = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc';
const OTHER_ACCOUNT_ID = 'dddddddd-dddd-4ddd-8ddd-dddddddddddd';

const githubData = {
    token: 'gh-token',
    github_id: '12345',
    username: 'octocat',
    avatar_url: 'https://example.com/avatar.png',
    email: 'octocat@example.com',
};

const userRow = (accountId: string | null) => ({
    id: USER_ID,
    email: githubData.email,
    user_name: githubData.username,
    role: 'super_admin',
    infra_id: [] as string[],
    account_id: accountId,
    created_at: new Date(),
    metadata: { github: { id: githubData.github_id } } as Record<string, unknown>,
    profile_url: null as string | null,
    async save() {
        return this;
    },
});

let invitedHolder: object | null = null;

const loginAs = async (row: ReturnType<typeof userRow>, accountLinkDeclined = false) => {
    const txSpy = mock.method(sequelize, 'transaction', async (cb: (t: object) => unknown) =>
        cb({}),
    );
    const findSpy = mock.method(User, 'findOne', async (opts?: { where?: object }) =>
        accountLinkDeclined && opts?.where && 'account_id' in opts.where
            ? { id: 'some-other-user-row' }
            : row,
    );
    const invitedSpy = mock.method(InvitedUser, 'findOne', async () => invitedHolder);
    const lockSpy = mock.method(sequelize, 'query', async () => []);
    const refreshSpy = mock.method(RefreshToken, 'create', async (attrs: object) => ({
        ...attrs,
        token_id: 'tok-1',
    }));
    try {
        return await service.upsertUser(githubData);
    } finally {
        for (const spy of [txSpy, findSpy, invitedSpy, lockSpy, refreshSpy]) spy.mock.restore();
    }
};

beforeEach(() => {
    store.orgs.clear();
    store.memberships.length = 0;
    store.published.length = 0;
    invitedHolder = null;
});

test('first login creates the personal org and owner membership once; the next login is a no-op', async () => {
    const orgId = personalOrgId(USER_ID);

    await loginAs(userRow(ACCOUNT_ID));

    assert.equal(store.orgs.get(orgId)?.owner_account_id, ACCOUNT_ID);
    assert.deepEqual(
        store.memberships.map((m) => [m.organization_id, m.account_id, m.role]),
        [[orgId, ACCOUNT_ID, 'owner']],
    );
    assert.equal(store.published.length, 1);
    assert.equal(store.published[0]!.routingKey, 'org.created');
    assert.equal(store.published[0]!.payload.organization_id, orgId);

    await loginAs(userRow(ACCOUNT_ID));

    assert.equal(store.orgs.size, 1);
    assert.equal(store.memberships.length, 1);
    assert.equal(store.published.length, 1);
});

test('login never takes over a personal org id owned by a different account, and still signs in', async () => {
    const orgId = personalOrgId(USER_ID);
    store.orgs.set(orgId, { id: orgId, owner_account_id: OTHER_ACCOUNT_ID, name: 'held' });

    const result = await loginAs(userRow(ACCOUNT_ID));

    assert.ok(result.accessToken);
    assert.equal(store.orgs.get(orgId)?.owner_account_id, OTHER_ACCOUNT_ID);
    assert.equal(store.memberships.length, 0);
    assert.equal(store.published.length, 0);
});

test('a user whose account link was declined gets no org', async () => {
    await loginAs(userRow(null), true);

    assert.equal(store.orgs.size, 0);
    assert.equal(store.published.length, 0);
});

test('a failure creating the org does not block sign-in', async () => {
    const failing = mock.method((await import('@/db')).Organization, 'findByPk', async () => {
        throw new Error('db down');
    });
    try {
        const result = await loginAs(userRow(ACCOUNT_ID));
        assert.ok(result.accessToken);
    } finally {
        failing.mock.restore();
    }
});

test('login creates no org while an invited row shares the same account', async () => {
    invitedHolder = { id: 'invited-row', account_id: ACCOUNT_ID };

    const result = await loginAs(userRow(ACCOUNT_ID));

    assert.ok(result.accessToken);
    assert.equal(store.orgs.size, 0);
    assert.equal(store.published.length, 0);
});

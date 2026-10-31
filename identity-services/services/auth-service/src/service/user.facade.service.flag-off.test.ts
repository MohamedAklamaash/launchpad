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
process.env.ORGS_ENABLED = 'false';

const { sequelize } = await import('@/db/sequalize');
const { Organization, Membership, RefreshToken, User } = await import('@/db');
const { UserFacadeService } = await import('@/service/user.facade.service');
const { orgEventsPublisher } = await import('@/messaging/producer/org-events.message');

const forbidden = (name: string) => async () => {
    throw new Error(`${name} must not be called with ORGS_ENABLED off`);
};

test('flag off: login touches no org table and publishes no org event', async () => {
    const txSpy = mock.method(sequelize, 'transaction', async (cb: (t: object) => unknown) =>
        cb({}),
    );
    const findOrgSpy = mock.method(Organization, 'findByPk', forbidden('Organization.findByPk'));
    const createOrgSpy = mock.method(Organization, 'create', forbidden('Organization.create'));
    const createMemberSpy = mock.method(Membership, 'create', forbidden('Membership.create'));
    const publishSpy = mock.method(orgEventsPublisher, 'publish', forbidden('publish'));
    const row = {
        id: '55555555-5555-4555-8555-555555555555',
        email: 'octocat@example.com',
        user_name: 'octocat',
        role: 'super_admin',
        infra_id: [] as string[],
        account_id: 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee',
        created_at: new Date(),
        metadata: { github: { id: '12345' } },
        profile_url: null as string | null,
        async save() {
            return this;
        },
    };
    const findUserSpy = mock.method(User, 'findOne', async () => row);
    const refreshSpy = mock.method(RefreshToken, 'create', async (attrs: object) => ({
        ...attrs,
        token_id: 'tok-1',
    }));

    try {
        const result = await new UserFacadeService().upsertUser({
            token: 'gh-token',
            github_id: '12345',
            username: 'octocat',
            avatar_url: 'https://example.com/avatar.png',
            email: 'octocat@example.com',
        });
        assert.ok(result.accessToken);
        assert.equal(findOrgSpy.mock.callCount(), 0);
        assert.equal(createOrgSpy.mock.callCount(), 0);
        assert.equal(createMemberSpy.mock.callCount(), 0);
        assert.equal(publishSpy.mock.callCount(), 0);
    } finally {
        for (const spy of [
            txSpy,
            findOrgSpy,
            createOrgSpy,
            createMemberSpy,
            publishSpy,
            findUserSpy,
            refreshSpy,
        ]) {
            spy.mock.restore();
        }
    }
});

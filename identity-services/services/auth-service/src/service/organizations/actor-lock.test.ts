import { test } from 'node:test';
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

const { actorStandingFor, assertOwnerForAdminChange } = await import('./actor-lock');

test('owner-only admin rule fails closed when no manager lock was taken for another account', () => {
    const standing = actorStandingFor(undefined, 'actor-1', 'target-1');

    assert.equal(standing, null);
    assert.throws(() => assertOwnerForAdminChange(standing, ['admin']), { statusCode: 403 });
    assert.doesNotThrow(() => assertOwnerForAdminChange(standing, ['developer', 'viewer']));
});

test('owner-only admin rule: locked admin is refused; owner, self and system actors pass', () => {
    assert.throws(() => assertOwnerForAdminChange(actorStandingFor('admin', 'a', 'b'), ['admin']), {
        statusCode: 403,
    });
    assert.doesNotThrow(() =>
        assertOwnerForAdminChange(actorStandingFor('owner', 'a', 'b'), ['admin']),
    );
    assert.doesNotThrow(() =>
        assertOwnerForAdminChange(actorStandingFor(undefined, 'a', 'a'), ['admin']),
    );
    assert.doesNotThrow(() =>
        assertOwnerForAdminChange(actorStandingFor(undefined, null, 'b'), ['admin']),
    );
});

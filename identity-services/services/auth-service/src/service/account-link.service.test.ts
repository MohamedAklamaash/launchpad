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

const { sequelize } = await import('@/db/sequalize');
const { Account, InvitedUser, User } = await import('@/db');
const { linkAccountForVerifiedIdentity } = await import('@/service/account-link.service');
const { accountIdForEmail } = await import('@/utils/account-id');

const fakeTransaction = { id: 'tx-1' } as never;

const traceLinkCalls = (existing: { user?: object; invited?: object } = {}) => {
    const calls: string[] = [];
    const queryArgs: Array<{ sql: string; opts: { replacements: { key: string } } }> = [];
    const spies = [
        mock.method(sequelize, 'query', async (sql: string, opts: never) => {
            calls.push('lock');
            queryArgs.push({ sql, opts });
            return [];
        }),
        mock.method(User, 'findOne', async () => {
            calls.push('read-user');
            return existing.user ?? null;
        }),
        mock.method(InvitedUser, 'findOne', async () => {
            calls.push('read-invited');
            return existing.invited ?? null;
        }),
        mock.method(Account, 'findOrCreate', async () => {
            calls.push('write-account');
            return [{}, true];
        }),
        mock.method(User, 'update', async () => {
            calls.push('write-user');
            return [1];
        }),
    ];
    return { calls, queryArgs, restore: () => spies.forEach((s) => s.mock.restore()) };
};

const input = {
    email: 'Person@Example.com',
    displayName: 'Person',
    table: 'users' as const,
    rowId: 'row-1',
    transaction: fakeTransaction,
};

test('link takes a transaction-scoped advisory lock on the account before reading or writing', async () => {
    const trace = traceLinkCalls();
    try {
        const accountId = await linkAccountForVerifiedIdentity(input);
        assert.equal(accountId, accountIdForEmail(input.email));

        assert.equal(trace.calls[0], 'lock');
        assert.deepEqual(trace.calls.slice(1, 3).sort(), ['read-invited', 'read-user']);
        assert.ok(trace.calls.indexOf('write-account') > 2);
        assert.match(trace.queryArgs[0]!.sql, /pg_advisory_xact_lock\(hashtext\(:key\)\)/);
        assert.ok(trace.queryArgs[0]!.opts.replacements.key.endsWith(accountId!));
    } finally {
        trace.restore();
    }
});

test('link locks on the same key for differently-cased emails and different tables', async () => {
    const keys: string[] = [];
    for (const [email, table] of [
        ['person@example.com', 'users'],
        [' PERSON@example.com ', 'invited_users'],
    ] as const) {
        const trace = traceLinkCalls();
        try {
            await linkAccountForVerifiedIdentity({ ...input, email, table });
            keys.push(trace.queryArgs[0]!.opts.replacements.key);
        } finally {
            trace.restore();
        }
    }
    assert.equal(keys[0], keys[1]);
});

test('link still declines, after taking the lock, when a different row holds the account', async () => {
    const trace = traceLinkCalls({ invited: { id: 'other-row' } });
    try {
        const accountId = await linkAccountForVerifiedIdentity(input);
        assert.equal(accountId, null);
        assert.equal(trace.calls[0], 'lock');
        assert.equal(trace.calls.includes('write-account'), false);
    } finally {
        trace.restore();
    }
});

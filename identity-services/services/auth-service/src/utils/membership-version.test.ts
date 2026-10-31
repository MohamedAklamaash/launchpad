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

// Drives the real bumpMembershipVersion against a fake single-row table, mocking only the
// Sequelize boundary (sequelize.query) — the same approach otp-attempts.ts's tests use for
// its own UPDATE ... RETURNING claim. A real Postgres row lock is what actually serializes
// concurrent callers; this test proves the arithmetic/contract bumpMembershipVersion relies
// on (always +1 off the stored value, never a client-computed guess) is correct.
const { sequelize } = await import('@/db/sequalize');
const { bumpMembershipVersion, bumpMembershipVersionForRemoval, reviveMembershipVersion } =
    await import('@/utils/membership-version');

const makeFakeRow = (initialVersion: number) => {
    let version = initialVersion;
    return {
        get version() {
            return version;
        },
        apply: () => {
            version += 1;
            return version;
        },
    };
};

test('bumpMembershipVersion is strictly monotonically increasing across sequential writes to the same row', async () => {
    const row = makeFakeRow(1);
    const querySpy = mock.method(sequelize, 'query', async () => [{ version: row.apply() }]);

    try {
        const first = await bumpMembershipVersion('membership-1', 'admin', {} as never);
        const second = await bumpMembershipVersion('membership-1', 'developer', {} as never);
        const third = await bumpMembershipVersion('membership-1', 'viewer', {} as never);

        assert.deepEqual([first, second, third], [2, 3, 4]);
        assert.ok(first < second);
        assert.ok(second < third);
    } finally {
        querySpy.mock.restore();
    }
});

test('bumpMembershipVersion never resets or decreases, even interleaved with another row', async () => {
    const rowA = makeFakeRow(1);
    const rowB = makeFakeRow(9);
    const querySpy = mock.method(
        sequelize,
        'query',
        async (_sql: string, opts: { replacements: { membershipId: string } }) => {
            const row = opts.replacements.membershipId === 'row-a' ? rowA : rowB;
            return [{ version: row.apply() }];
        },
    );

    try {
        const a1 = await bumpMembershipVersion('row-a', 'admin', {} as never);
        const b1 = await bumpMembershipVersion('row-b', 'admin', {} as never);
        const a2 = await bumpMembershipVersion('row-a', 'viewer', {} as never);
        const b2 = await bumpMembershipVersion('row-b', 'viewer', {} as never);

        assert.deepEqual([a1, a2], [2, 3]);
        assert.deepEqual([b1, b2], [10, 11]);
    } finally {
        querySpy.mock.restore();
    }
});

test('bumpMembershipVersion throws rather than silently no-op when the row does not exist', async () => {
    const querySpy = mock.method(sequelize, 'query', async () => []);
    try {
        await assert.rejects(
            () => bumpMembershipVersion('missing-row', 'admin', {} as never),
            /not found/i,
        );
    } finally {
        querySpy.mock.restore();
    }
});

// ── bumpMembershipVersionForRemoval ──────────────────────────────────────────────────────

test('bumpMembershipVersionForRemoval continues the same monotonic sequence a role change was already on', async () => {
    const row = makeFakeRow(4);
    const querySpy = mock.method(sequelize, 'query', async () => [{ version: row.apply() }]);

    try {
        const version = await bumpMembershipVersionForRemoval('membership-1', {} as never);
        assert.equal(version, 5);
    } finally {
        querySpy.mock.restore();
    }
});

test('bumpMembershipVersionForRemoval throws rather than silently no-op when the row does not exist', async () => {
    const querySpy = mock.method(sequelize, 'query', async () => []);
    try {
        await assert.rejects(
            () => bumpMembershipVersionForRemoval('missing-row', {} as never),
            /not found/i,
        );
    } finally {
        querySpy.mock.restore();
    }
});

// ── reviveMembershipVersion ───────────────────────────────────────────────────────────────

test('reviveMembershipVersion continues the version sequence a tombstone left off at, never restarting at 1', async () => {
    // The row was tombstoned at version 6 (a removal); reviving it must bump past that,
    // not reset — this is the whole fix for the resurrection race (plan/F9-teams-and-
    // orgs.md): a stale event from before the removal (any version <= 6) must still be
    // rejected by the revived row.
    const row = makeFakeRow(6);
    const querySpy = mock.method(sequelize, 'query', async () => [{ version: row.apply() }]);

    try {
        const version = await reviveMembershipVersion('membership-1', 'developer', {} as never);
        assert.equal(version, 7);
    } finally {
        querySpy.mock.restore();
    }
});

test('reviveMembershipVersion throws rather than silently no-op when the row does not exist', async () => {
    const querySpy = mock.method(sequelize, 'query', async () => []);
    try {
        await assert.rejects(
            () => reviveMembershipVersion('missing-row', 'developer', {} as never),
            /not found/i,
        );
    } finally {
        querySpy.mock.restore();
    }
});

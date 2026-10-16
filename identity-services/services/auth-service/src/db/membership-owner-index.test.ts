import { test, mock } from 'node:test';
import assert from 'node:assert/strict';

// env.ts validates process.env at import time via zod — dummy values only, never real
// secrets. Same preamble as account-backfill.service.test.ts.
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

// This suite does not have a real Postgres to run migrations against (constraint for this
// change), so the DB-level "inserting a second owner row fails" invariant is verified two
// ways instead of by provoking a real unique-violation:
//   1. here — the exact SQL this service applies on boot (db/index.ts's
//      applyPendingSchemaPatches) declares a partial unique index that makes a second
//      `owner` row per organization_id impossible at the Postgres level.
//   2. src/service/organizations/membership-owner-guard.test.ts — the service-level guard
//      that stops application code from ever attempting it in the first place.
const { sequelize } = await import('@/db/sequalize');
const { initModels, MEMBERSHIPS_ONE_OWNER_PER_ORG_INDEX_SQL } = await import('@/db');

test('the memberships owner partial unique index is scoped to role = owner, per organization_id, and idempotent', () => {
    const sql = MEMBERSHIPS_ONE_OWNER_PER_ORG_INDEX_SQL;

    assert.match(sql, /CREATE UNIQUE INDEX IF NOT EXISTS/i);
    assert.match(sql, /ON memberships \(organization_id\)/i);
    assert.match(sql, /WHERE role = 'owner'/i);
});

test('initModels actually issues the owner-index SQL on boot, not just defines the constant', async () => {
    const syncSpy = mock.method(sequelize, 'sync', async () => undefined);
    const queries: string[] = [];
    const querySpy = mock.method(sequelize, 'query', async (sql: string) => {
        queries.push(sql);
        return [];
    });

    try {
        await initModels();
        assert.ok(
            queries.includes(MEMBERSHIPS_ONE_OWNER_PER_ORG_INDEX_SQL),
            'applyPendingSchemaPatches must run the owner-index SQL during initModels',
        );
    } finally {
        syncSpy.mock.restore();
        querySpy.mock.restore();
    }
});

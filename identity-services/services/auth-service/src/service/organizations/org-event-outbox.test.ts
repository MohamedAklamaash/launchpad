import { test, mock } from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';

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

const { Op } = await import('sequelize');
const { sequelize } = await import('@/db/sequalize');
const { OrgEventOutbox } = await import('@/db');
const { orgEventsPublisher } = await import('@/messaging/producer/org-events.message');
const { drainOutbox, drainOutboxFully, enqueueMembershipChanged, nextDispatchDelay } =
    await import('@/service/organizations/org-event-outbox.service');

interface Row {
    seq: string;
    routing_key: string;
    payload: Record<string, unknown>;
    created_at: Date;
    sent_at: Date | null;
}

const rig = () => {
    const rows: Row[] = [];
    const published: string[] = [];
    const control = {
        connected: true,
        lockHeld: true,
        confirmError: null as Error | null,
        losses: 0,
        buffered: 0,
        loseChannelOnPublish: false,
    };
    const mocks = [
        mock.method(sequelize, 'transaction', async (cb: (t: object) => unknown) => {
            const snapshot = rows.map((row) => ({ ...row }));
            try {
                return await cb({});
            } catch (error) {
                rows.forEach((row, i) => Object.assign(row, snapshot[i]));
                throw error;
            }
        }),
        mock.method(sequelize, 'query', async () => [{ locked: control.lockHeld }]),
        mock.method(
            OrgEventOutbox,
            'create',
            async (attrs: Pick<Row, 'routing_key' | 'payload'>) => {
                rows.push({
                    ...attrs,
                    seq: String(rows.length + 1),
                    created_at: new Date(),
                    sent_at: null,
                });
            },
        ),
        mock.method(OrgEventOutbox, 'findAll', async (o: { limit: number }) =>
            rows.filter((r) => r.sent_at === null).slice(0, o.limit),
        ),
        mock.method(
            OrgEventOutbox,
            'update',
            async (values: { sent_at: Date }, o: { where: { seq: Record<symbol, string[]> } }) => {
                rows.filter((r) => o.where.seq[Op.in]!.includes(r.seq)).forEach((r) =>
                    Object.assign(r, values),
                );
            },
        ),
        mock.method(
            OrgEventOutbox,
            'count',
            async () => rows.filter((r) => r.sent_at === null).length,
        ),
        mock.method(orgEventsPublisher, 'isConnected', () => control.connected),
        mock.method(orgEventsPublisher, 'publish', (_key: string, body: Buffer) => {
            const event = JSON.parse(body.toString()) as {
                payload: { version: number; account_id: string };
            };
            published.push(`${event.payload.account_id}:${event.payload.version}`);
            if (control.loseChannelOnPublish) control.losses += 1;
        }),
        mock.method(orgEventsPublisher, 'waitForConfirms', async () => {
            if (control.confirmError) throw control.confirmError;
        }),
        mock.method(orgEventsPublisher, 'confirmChannelLossCount', () => control.losses),
        mock.method(orgEventsPublisher, 'bufferedWhileDisconnectedCount', () => control.buffered),
    ];
    const enqueue = (account: string, version: number) =>
        enqueueMembershipChanged({} as never, {
            organization_id: randomUUID(),
            account_id: account,
            role: 'admin',
            version,
        });
    return {
        rows,
        published,
        control,
        enqueue,
        restore: () => mocks.forEach((m) => m.mock.restore()),
    };
};

test('pending rows are published in insertion order and marked sent only after the confirm', async () => {
    const r = rig();
    try {
        await r.enqueue('a', 1);
        await r.enqueue('b', 2);
        await r.enqueue('a', 3);

        const result = await drainOutbox();

        assert.deepEqual(result, { status: 'published', published: 3 });
        assert.deepEqual(r.published, ['a:1', 'b:2', 'a:3']);
        assert.ok(r.rows.every((row) => row.sent_at !== null));
        assert.equal((await drainOutbox()).status, 'idle');
    } finally {
        r.restore();
    }
});

test('an unconfirmed publish leaves every row pending, and the next run re-sends them in order', async () => {
    const r = rig();
    try {
        await r.enqueue('a', 1);
        await r.enqueue('b', 2);
        r.control.confirmError = new Error('nacked');

        await assert.rejects(drainOutbox(), /nacked/);
        assert.ok(r.rows.every((row) => row.sent_at === null));

        r.control.confirmError = null;
        r.published.length = 0;
        await drainOutbox();
        assert.deepEqual(r.published, ['a:1', 'b:2']);
        assert.ok(r.rows.every((row) => row.sent_at !== null));
    } finally {
        r.restore();
    }
});

test('rows committed before a crash are delivered by the next dispatcher run', async () => {
    const r = rig();
    try {
        await r.enqueue('a', 1);
        await r.enqueue('b', 1);
        assert.equal(r.published.length, 0);

        const pendingAfterRestart = await drainOutboxFully();

        assert.equal(pendingAfterRestart, 0);
        assert.deepEqual(r.published, ['a:1', 'b:1']);
    } finally {
        r.restore();
    }
});

test('a channel lost mid-batch means the batch is not trusted and stays pending', async () => {
    const r = rig();
    try {
        await r.enqueue('a', 1);
        r.control.loseChannelOnPublish = true;

        await assert.rejects(drainOutbox(), /channel was lost/);

        assert.equal(r.rows[0]!.sent_at, null);
    } finally {
        r.restore();
    }
});

test('nothing is published while disconnected or while another dispatcher holds the lock', async () => {
    const r = rig();
    try {
        await r.enqueue('a', 1);
        r.control.connected = false;
        assert.equal((await drainOutbox()).status, 'disconnected');
        r.control.connected = true;
        r.control.lockHeld = false;
        assert.equal((await drainOutbox()).status, 'busy');

        assert.equal(r.published.length, 0);
        assert.equal(r.rows[0]!.sent_at, null);
        assert.equal(await drainOutboxFully(2), 1);
    } finally {
        r.restore();
    }
});

test('dispatch backoff grows on failure, is capped, and polls again immediately only when a batch was full', () => {
    assert.equal(nextDispatchDelay(1, 0), 1000);
    assert.equal(nextDispatchDelay(3, 0), 4000);
    assert.equal(nextDispatchDelay(20, 0), 30_000);
    assert.equal(nextDispatchDelay(0, 100), 0);
    assert.equal(nextDispatchDelay(0, 3), 500);
});

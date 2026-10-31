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

const { orgEventsPublisher, verifyOrgEventsDelivered } =
    await import('@/messaging/producer/org-events.message');

const stubPublisher = (state: {
    pending: number;
    buffered: number;
    losses?: number;
    confirmError?: Error;
}) => [
    mock.method(orgEventsPublisher, 'confirmChannelLossCount', () => state.losses ?? 0),
    mock.method(orgEventsPublisher, 'pendingCount', () => state.pending),
    mock.method(orgEventsPublisher, 'bufferedWhileDisconnectedCount', () => state.buffered),
    mock.method(orgEventsPublisher, 'waitForConfirms', async () => {
        if (state.confirmError) throw state.confirmError;
    }),
];

const restoreAll = (spies: ReturnType<typeof stubPublisher>) =>
    spies.forEach((spy) => spy.mock.restore());

test('delivered when everything was confirmed and nothing was ever buffered', async () => {
    const spies = stubPublisher({ pending: 0, buffered: 0 });
    try {
        assert.equal((await verifyOrgEventsDelivered()).delivered, true);
    } finally {
        restoreAll(spies);
    }
});

test('not delivered when messages are still buffered', async () => {
    const spies = stubPublisher({ pending: 3, buffered: 3 });
    try {
        const report = await verifyOrgEventsDelivered();
        assert.equal(report.delivered, false);
        assert.equal(report.pending, 3);
    } finally {
        restoreAll(spies);
    }
});

test('not delivered when any publish happened while disconnected, even if later flushed', async () => {
    const spies = stubPublisher({ pending: 0, buffered: 1 });
    try {
        assert.equal((await verifyOrgEventsDelivered()).delivered, false);
    } finally {
        restoreAll(spies);
    }
});

test('not delivered when the broker nacks or the confirm wait fails', async () => {
    const spies = stubPublisher({ pending: 0, buffered: 0, confirmError: new Error('nacked') });
    try {
        const report = await verifyOrgEventsDelivered();
        assert.equal(report.delivered, false);
        assert.equal(report.error, 'nacked');
    } finally {
        restoreAll(spies);
    }
});

test('not delivered when the connection dropped and reconnected during the run, even if the new channel confirms', async () => {
    const spies = stubPublisher({ pending: 0, buffered: 0, losses: 1 });
    try {
        const report = await verifyOrgEventsDelivered();
        assert.equal(report.delivered, false);
        assert.equal(report.connectionLosses, 1);
    } finally {
        restoreAll(spies);
    }
});

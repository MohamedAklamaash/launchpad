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

// otp-attempts.ts opens a real ioredis connection at module load (lazyConnect defers
// the actual socket until the first command) — mock Redis.prototype directly so no
// command here ever touches the network, the same way other tests mock a class
// prototype (e.g. InvitedUserFacade.prototype) instead of the concrete instance.
const Redis = (await import('ioredis')).default;
const { otpAttempts } = await import('@/utils/otp-attempts');

const withStore = () => {
    const store = new Map<string, { value: string; expiresAt?: number }>();

    const get = mock.method(Redis.prototype, 'get', async function (key: string) {
        const entry = store.get(key);
        if (!entry) return null;
        if (entry.expiresAt && entry.expiresAt < Date.now()) {
            store.delete(key);
            return null;
        }
        return entry.value;
    });
    const incr = mock.method(Redis.prototype, 'incr', async function (key: string) {
        const current = Number(store.get(key)?.value ?? '0') + 1;
        store.set(key, { value: String(current), expiresAt: store.get(key)?.expiresAt });
        return current;
    });
    const expire = mock.method(
        Redis.prototype,
        'expire',
        async function (key: string, seconds: number) {
            const entry = store.get(key);
            if (entry) entry.expiresAt = Date.now() + seconds * 1000;
            return 1;
        },
    );
    const del = mock.method(Redis.prototype, 'del', async function (key: string) {
        return store.delete(key) ? 1 : 0;
    });

    return {
        store,
        restore: () => {
            get.mock.restore();
            incr.mock.restore();
            expire.mock.restore();
            del.mock.restore();
        },
    };
};

test('hasAttemptsRemaining is true with no prior attempts', async () => {
    const { restore } = withStore();
    try {
        assert.equal(await otpAttempts.hasAttemptsRemaining('register', 'a@example.com'), true);
    } finally {
        restore();
    }
});

test('five recorded failures still leave the cap unhit, the sixth trips it', async () => {
    const { restore } = withStore();
    try {
        const email = 'brute-force@example.com';
        for (let i = 0; i < 4; i++) {
            await otpAttempts.recordFailedAttempt('register', email);
            assert.equal(
                await otpAttempts.hasAttemptsRemaining('register', email),
                true,
                `expected attempts remaining after ${i + 1} failures`,
            );
        }
        // 5th failure hits the cap.
        await otpAttempts.recordFailedAttempt('register', email);
        assert.equal(await otpAttempts.hasAttemptsRemaining('register', email), false);
    } finally {
        restore();
    }
});

test('clearAttempts resets the counter', async () => {
    const { restore } = withStore();
    try {
        const email = 'reset-me@example.com';
        for (let i = 0; i < 5; i++) await otpAttempts.recordFailedAttempt('register', email);
        assert.equal(await otpAttempts.hasAttemptsRemaining('register', email), false);

        await otpAttempts.clearAttempts('register', email);
        assert.equal(await otpAttempts.hasAttemptsRemaining('register', email), true);
    } finally {
        restore();
    }
});

test('attempts are scoped per purpose — register and password-reset never share a bucket', async () => {
    const { restore } = withStore();
    try {
        const email = 'multi-flow@example.com';
        for (let i = 0; i < 5; i++) await otpAttempts.recordFailedAttempt('register', email);

        assert.equal(await otpAttempts.hasAttemptsRemaining('register', email), false);
        assert.equal(await otpAttempts.hasAttemptsRemaining('password-reset', email), true);
    } finally {
        restore();
    }
});

test('attempts are scoped per email — one address hitting the cap does not affect another', async () => {
    const { restore } = withStore();
    try {
        for (let i = 0; i < 5; i++)
            await otpAttempts.recordFailedAttempt('register', 'victim@example.com');

        assert.equal(
            await otpAttempts.hasAttemptsRemaining('register', 'victim@example.com'),
            false,
        );
        assert.equal(
            await otpAttempts.hasAttemptsRemaining('register', 'someone-else@example.com'),
            true,
        );
    } finally {
        restore();
    }
});

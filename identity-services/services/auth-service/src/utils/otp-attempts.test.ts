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
// command here ever touches the network, and mock sequelize.query so the DB-backed
// attempt cap never touches a live Postgres either.
const Redis = (await import('ioredis')).default;
const { sequelize } = await import('@/db/sequalize');
const { otpAttempts, MAX_OTP_ATTEMPTS, NIL_INVITED_USER_ID } = await import('@/utils/otp-attempts');

interface FakeAccount {
    id: string;
    email: string;
    user_name: string;
    role: string;
    roles: Record<string, string>;
    infra_id: string[];
    created_at: Date;
    failed_otp_attempts: number;
}

// Models the single atomic UPDATE ... RETURNING (claimAttempt) and the plain reset
// UPDATE (resetAttempts) against a Map instead of Postgres — window-expiry (a live
// concern for real concurrent traffic) is out of scope here since it needs wall-clock
// Postgres semantics to test meaningfully; this exercises otpAttempts's own call
// sequencing and per-account isolation.
const withAccountsStore = (accounts: FakeAccount[]) => {
    const byEmail = new Map(accounts.map((a) => [a.email, { ...a }]));
    const byId = new Map(accounts.map((a) => [a.id, byEmail.get(a.email)!]));

    const querySpy = mock.method(
        sequelize,
        'query',
        async (sql: string, opts: { replacements?: Record<string, string> }) => {
            if (sql.includes('RETURNING')) {
                const row = byEmail.get(opts.replacements!.email);
                if (!row) return [];
                row.failed_otp_attempts += 1;
                return [{ ...row }];
            }
            const row = byId.get(opts.replacements!.userId);
            if (row) row.failed_otp_attempts = 0;
            return [];
        },
    );

    return { restore: () => querySpy.mock.restore() };
};

const account = (overrides: Partial<FakeAccount> = {}): FakeAccount => ({
    id: 'user-1',
    email: 'a@example.com',
    user_name: 'a',
    role: 'user',
    roles: { 'infra-1': 'user' },
    infra_id: ['infra-1'],
    created_at: new Date('2026-01-01T00:00:00Z'),
    failed_otp_attempts: 0,
    ...overrides,
});

test('claimAttempt returns null for an email with no account, without incrementing anything', async () => {
    const { restore } = withAccountsStore([account()]);
    try {
        const claim = await otpAttempts.claimAttempt('ghost@example.com');
        assert.equal(claim, null);
    } finally {
        restore();
    }
});

test('claimAttempt returns the account row and an incrementing attempt count', async () => {
    const { restore } = withAccountsStore([account()]);
    try {
        const first = await otpAttempts.claimAttempt('a@example.com');
        const second = await otpAttempts.claimAttempt('a@example.com');
        assert.equal(first?.userId, 'user-1');
        assert.equal(first?.attempts, 1);
        assert.equal(second?.attempts, 2);
    } finally {
        restore();
    }
});

test('the first MAX_OTP_ATTEMPTS claims stay at or under the cap, the next one exceeds it', async () => {
    const { restore } = withAccountsStore([account()]);
    try {
        let last;
        for (let i = 0; i < MAX_OTP_ATTEMPTS; i++) {
            last = await otpAttempts.claimAttempt('a@example.com');
            assert.ok(last!.attempts <= MAX_OTP_ATTEMPTS, `claim ${i + 1} should be within cap`);
        }
        const overCap = await otpAttempts.claimAttempt('a@example.com');
        assert.ok(overCap!.attempts > MAX_OTP_ATTEMPTS);
    } finally {
        restore();
    }
});

test('resetAttempts zeroes the counter for that account only', async () => {
    const { restore } = withAccountsStore([
        account(),
        account({ id: 'user-2', email: 'b@example.com' }),
    ]);
    try {
        for (let i = 0; i < MAX_OTP_ATTEMPTS; i++) await otpAttempts.claimAttempt('a@example.com');
        await otpAttempts.claimAttempt('b@example.com');

        await otpAttempts.resetAttempts('user-1');

        const afterReset = await otpAttempts.claimAttempt('a@example.com');
        const other = await otpAttempts.claimAttempt('b@example.com');
        assert.equal(afterReset?.attempts, 1);
        assert.equal(other?.attempts, 2);
    } finally {
        restore();
    }
});

test('NIL_INVITED_USER_ID is a well-formed UUID distinct from any real account id', () => {
    assert.match(
        NIL_INVITED_USER_ID,
        /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/,
    );
});

// ── shouldThrottleForgotPassword: unchanged, still Redis-backed ────────────────────

const withRedisStore = () => {
    const store = new Map<string, { value: number; expiresAt?: number }>();
    const evalMock = mock.method(
        Redis.prototype,
        'eval',
        async function (_script: string, _numKeys: number, key: string, windowSeconds: string) {
            const existing = store.get(key);
            const isLive = existing && (!existing.expiresAt || existing.expiresAt > Date.now());
            const count = (isLive ? existing.value : 0) + 1;
            store.set(key, {
                value: count,
                expiresAt:
                    count === 1 ? Date.now() + Number(windowSeconds) * 1000 : existing?.expiresAt,
            });
            return count;
        },
    );
    return { restore: () => evalMock.mock.restore() };
};

test('shouldThrottleForgotPassword allows a single request, then blocks within the minute window', async () => {
    const { restore } = withRedisStore();
    try {
        assert.equal(await otpAttempts.shouldThrottleForgotPassword('a@example.com'), false);
        assert.equal(await otpAttempts.shouldThrottleForgotPassword('a@example.com'), true);
    } finally {
        restore();
    }
});

test('shouldThrottleForgotPassword throttles per email, not globally', async () => {
    const { restore } = withRedisStore();
    try {
        assert.equal(await otpAttempts.shouldThrottleForgotPassword('victim@example.com'), false);
        assert.equal(await otpAttempts.shouldThrottleForgotPassword('victim@example.com'), true);
        assert.equal(await otpAttempts.shouldThrottleForgotPassword('other@example.com'), false);
    } finally {
        restore();
    }
});

test('shouldThrottleForgotPassword normalizes email casing to the same bucket', async () => {
    const { restore } = withRedisStore();
    try {
        assert.equal(await otpAttempts.shouldThrottleForgotPassword('A@Example.com'), false);
        assert.equal(await otpAttempts.shouldThrottleForgotPassword('a@example.com'), true);
    } finally {
        restore();
    }
});

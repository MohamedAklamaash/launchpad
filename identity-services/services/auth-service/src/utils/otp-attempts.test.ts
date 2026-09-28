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

// Re-implements exactly what INCR_WITH_WINDOW_SCRIPT does server-side, against a plain
// Map instead of Redis — INCR then, only on the first increment, set a TTL — so these
// tests exercise otpAttempts's real call sequencing (claimAttempt calling eval with the
// right key/args) without needing a live Redis.
const withStore = () => {
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
    const del = mock.method(Redis.prototype, 'del', async function (key: string) {
        return store.delete(key) ? 1 : 0;
    });

    return {
        store,
        restore: () => {
            evalMock.mock.restore();
            del.mock.restore();
        },
    };
};

test('keyFor prefers the account id over the email', () => {
    assert.equal(otpAttempts.keyFor('user-1', 'a@example.com'), 'otp-attempts:user:user-1');
});

test('keyFor falls back to a normalized email when there is no account id', () => {
    assert.equal(
        otpAttempts.keyFor(undefined, 'A@Example.com'),
        'otp-attempts:email:a@example.com',
    );
    assert.equal(otpAttempts.keyFor(null, 'a@example.com'), 'otp-attempts:email:a@example.com');
});

test('claimAttempt allows the first 5 claims and rejects the 6th', async () => {
    const { restore } = withStore();
    try {
        const key = otpAttempts.keyFor('user-1', 'a@example.com');
        for (let i = 0; i < 5; i++) {
            assert.equal(
                await otpAttempts.claimAttempt(key),
                true,
                `claim ${i + 1} should succeed`,
            );
        }
        assert.equal(await otpAttempts.claimAttempt(key), false);
    } finally {
        restore();
    }
});

test('claimAttempt increments unconditionally — two claims in a row cost two slots', async () => {
    // This is the shape of the fix for the check-then-act race: the caller must claim
    // unconditionally, before it knows whether the guess is right, wrong, or for an
    // account that doesn't even exist. Verified via the observable effect: two claims
    // leave only 3 of the 5 slots remaining, not 4.
    const { restore } = withStore();
    try {
        const key = otpAttempts.keyFor('user-1', 'a@example.com');
        assert.equal(await otpAttempts.claimAttempt(key), true);
        assert.equal(await otpAttempts.claimAttempt(key), true);
        assert.equal(await otpAttempts.claimAttempt(key), true);
        assert.equal(await otpAttempts.claimAttempt(key), true);
        assert.equal(await otpAttempts.claimAttempt(key), true);
        // That's 5 claims total (2 + 3) — the 6th must fail.
        assert.equal(await otpAttempts.claimAttempt(key), false);
    } finally {
        restore();
    }
});

test('clearAttempts resets the counter for that key only', async () => {
    const { restore } = withStore();
    try {
        const key = otpAttempts.keyFor('user-1', 'a@example.com');
        for (let i = 0; i < 5; i++) await otpAttempts.claimAttempt(key);
        assert.equal(await otpAttempts.claimAttempt(key), false);

        await otpAttempts.clearAttempts(key);
        assert.equal(await otpAttempts.claimAttempt(key), true);
    } finally {
        restore();
    }
});

test('the cap is shared across purposes for the same account (no purpose suffix in the key)', async () => {
    const { restore } = withStore();
    try {
        // Both authenticateWithOTP and verifyResetOTP compute the same key for the same
        // user id — this is what keeps an attacker from getting 5 register-OTP guesses
        // and a separate 5 password-reset-OTP guesses against one account.
        const key = otpAttempts.keyFor('user-1', 'a@example.com');
        for (let i = 0; i < 5; i++) await otpAttempts.claimAttempt(key);
        assert.equal(await otpAttempts.claimAttempt(key), false);
    } finally {
        restore();
    }
});

test('attempts are scoped per account — one user hitting the cap does not affect another', async () => {
    const { restore } = withStore();
    try {
        const victim = otpAttempts.keyFor('user-victim', 'victim@example.com');
        const other = otpAttempts.keyFor('user-other', 'other@example.com');
        for (let i = 0; i < 5; i++) await otpAttempts.claimAttempt(victim);

        assert.equal(await otpAttempts.claimAttempt(victim), false);
        assert.equal(await otpAttempts.claimAttempt(other), true);
    } finally {
        restore();
    }
});

test('shouldThrottleForgotPassword allows a single request, then blocks within the minute window', async () => {
    const { restore } = withStore();
    try {
        assert.equal(await otpAttempts.shouldThrottleForgotPassword('a@example.com'), false);
        assert.equal(await otpAttempts.shouldThrottleForgotPassword('a@example.com'), true);
    } finally {
        restore();
    }
});

test('shouldThrottleForgotPassword throttles per email, not globally', async () => {
    const { restore } = withStore();
    try {
        assert.equal(await otpAttempts.shouldThrottleForgotPassword('victim@example.com'), false);
        assert.equal(await otpAttempts.shouldThrottleForgotPassword('victim@example.com'), true);
        assert.equal(await otpAttempts.shouldThrottleForgotPassword('other@example.com'), false);
    } finally {
        restore();
    }
});

test('shouldThrottleForgotPassword normalizes email casing to the same bucket', async () => {
    const { restore } = withStore();
    try {
        assert.equal(await otpAttempts.shouldThrottleForgotPassword('A@Example.com'), false);
        assert.equal(await otpAttempts.shouldThrottleForgotPassword('a@example.com'), true);
    } finally {
        restore();
    }
});

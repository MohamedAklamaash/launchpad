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

const Redis = (await import('ioredis')).default;
const { HttpError } = await import('@launchpad/common');
const { sequelize } = await import('@/db/sequalize');
const { MAX_INVITES_PER_WINDOW } = await import('@/utils/otp-attempts');
const { InvitedUser, UserOTP } = await import('@/db');
const messageModule = await import('@/messaging/producer/user-created.message');
const { InvitedUserService } = await import('@/service/invited-users/inviter-user.crud.service');

const withTransactionMock = () =>
    mock.method(sequelize, 'transaction', async (cb: (t: object) => unknown) =>
        cb({ afterCommit: () => undefined }),
    );

const service = new InvitedUserService();

// otp-attempts.ts opens an ioredis client at import; stub eval so no test reaches a real Redis.
const withRedisCounter = () => {
    const counts = new Map<string, number>();
    const evalSpy = mock.method(
        Redis.prototype,
        'eval',
        async function (_script: string, _numKeys: number, key: string) {
            const next = (counts.get(key) ?? 0) + 1;
            counts.set(key, next);
            return next;
        },
    );
    return { counts, restore: () => evalSpy.mock.restore() };
};

const inviteInput = (email: string) => ({
    email,
    password: 'secret123',
    user_name: 'a',
    infra_id: 'infra-1',
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    role: 'user' as any,
});

test('register: createOTP dedupes any prior register-purpose OTP before minting a new one', async () => {
    const redis = withRedisCounter();
    const txSpy = withTransactionMock();
    const findUserSpy = mock.method(InvitedUser, 'findOne', async () => null);
    const createUserSpy = mock.method(InvitedUser, 'create', async () => ({
        id: 'user-1',
        email: 'a@example.com',
        user_name: 'a',
        infra_id: ['infra-1'],
        roles: { 'infra-1': 'user' },
        role: 'user',
        created_at: new Date(),
        updated_at: new Date(),
    }));
    const destroyOtpSpy = mock.method(UserOTP, 'destroy', async () => 0);
    const createOtpSpy = mock.method(UserOTP, 'create', async () => ({ otp: '123456' }));
    const queueSpy = mock.method(
        messageModule.userAuthenticationQueue,
        'add',
        async () => undefined as never,
    );
    // PublishUserRegistered is a plain function export, not mockable in this ESM/esbuild
    // setup (Cannot redefine property) — register() already wraps its call in a
    // try/catch that only logs, so letting it run for real (and fail, since there's no
    // live AMQP connection in tests) doesn't affect this test's outcome.
    const originalConsoleError = console.error;
    console.error = () => {};

    try {
        const { otp } = await service.register(
            {
                email: 'a@example.com',
                password: 'secret123',
                user_name: 'a',
                infra_id: 'infra-1',
                // eslint-disable-next-line @typescript-eslint/no-explicit-any
                role: 'user' as any,
            },
            'super-user-1',
        );

        assert.equal(otp, '123456');
        assert.equal(destroyOtpSpy.mock.callCount(), 1);
        const destroyArgs = destroyOtpSpy.mock.calls[0]?.arguments[0] as { where: unknown };
        assert.deepEqual(destroyArgs.where, {
            invited_user_id: 'user-1',
            purpose: 'register',
        });
        const createArgs = createOtpSpy.mock.calls[0]?.arguments[0] as { purpose: string };
        assert.equal(createArgs.purpose, 'register');

        // R2: the plaintext-OTP job must not linger in Redis after BullMQ processes it.
        const jobOptions = queueSpy.mock.calls[0]?.arguments[2] as Record<string, unknown>;
        assert.equal(jobOptions.removeOnComplete, true);
        assert.deepEqual(jobOptions.removeOnFail, { age: 3600 });
    } finally {
        console.error = originalConsoleError;
        redis.restore();
        txSpy.mock.restore();
        findUserSpy.mock.restore();
        createUserSpy.mock.restore();
        destroyOtpSpy.mock.restore();
        createOtpSpy.mock.restore();
        queueSpy.mock.restore();
    }
});

test("register: inviting an already-invited-but-unverified user to a second infra invalidates the first infra's still-live code (security review R2)", async () => {
    // R2: this used to be scoped to (user, purpose, infra_id) specifically so infra-1's
    // still-live code would survive an infra-2 invite — but the OTP verify lookup
    // (authenticateWithOTP) matches ANY live REGISTER code for the user, not one scoped
    // to a specific infra, so K simultaneous invites handed an attacker K codes to guess
    // against the same account at once. Deduped by (user, purpose) alone now: inviting
    // to infra-2 replaces whichever REGISTER code (infra-1's) was still outstanding, so
    // there is never more than one live code per user.
    const redis = withRedisCounter();
    const txSpy = withTransactionMock();
    const existingUser = {
        id: 'user-1',
        email: 'a@example.com',
        user_name: 'a',
        infra_id: ['infra-1'],
        roles: { 'infra-1': 'user' },
        role: 'user',
        is_authenticated: false,
        async save() {
            return this;
        },
    };
    const findUserSpy = mock.method(InvitedUser, 'findOne', async (opts: { where: object }) =>
        'email' in (opts.where as Record<string, unknown>) ? existingUser : null,
    );
    const destroyOtpSpy = mock.method(UserOTP, 'destroy', async () => 0);
    const createOtpSpy = mock.method(UserOTP, 'create', async () => ({ otp: '654321' }));
    const queueSpy = mock.method(
        messageModule.userAuthenticationQueue,
        'add',
        async () => undefined as never,
    );
    const originalConsoleError = console.error;
    console.error = () => {};

    try {
        await service.register(
            {
                email: 'a@example.com',
                password: 'secret123',
                user_name: 'a',
                infra_id: 'infra-2',
                // eslint-disable-next-line @typescript-eslint/no-explicit-any
                role: 'user' as any,
            },
            'super-user-1',
        );

        assert.equal(destroyOtpSpy.mock.callCount(), 1);
        const destroyArgs = destroyOtpSpy.mock.calls[0]?.arguments[0] as { where: unknown };
        assert.deepEqual(destroyArgs.where, {
            invited_user_id: 'user-1',
            purpose: 'register',
        });
    } finally {
        console.error = originalConsoleError;
        redis.restore();
        txSpy.mock.restore();
        findUserSpy.mock.restore();
        destroyOtpSpy.mock.restore();
        createOtpSpy.mock.restore();
        queueSpy.mock.restore();
    }
});

test('register: the invite after the per-email cap is rejected with 429 and sends nothing', async () => {
    const redis = withRedisCounter();
    const txSpy = withTransactionMock();
    const findUserSpy = mock.method(InvitedUser, 'findOne', async () => null);
    const createUserSpy = mock.method(InvitedUser, 'create', async () => ({
        id: 'user-1',
        email: 'victim@example.com',
        infra_id: ['infra-1'],
        roles: { 'infra-1': 'user' },
        role: 'user',
        created_at: new Date(),
        updated_at: new Date(),
    }));
    const destroyOtpSpy = mock.method(UserOTP, 'destroy', async () => 0);
    const createOtpSpy = mock.method(UserOTP, 'create', async () => ({ otp: '123456' }));
    const queueSpy = mock.method(
        messageModule.userAuthenticationQueue,
        'add',
        async () => undefined as never,
    );
    const originalConsoleError = console.error;
    console.error = () => {};

    try {
        for (let i = 0; i < MAX_INVITES_PER_WINDOW; i++) {
            await service.register(inviteInput('victim@example.com'), 'super-user-1');
        }
        const sentBefore = queueSpy.mock.callCount();

        await assert.rejects(
            () => service.register(inviteInput(' Victim@Example.com '), 'super-user-1'),
            (err: unknown) => err instanceof HttpError && err.statusCode === 429,
        );
        assert.equal(queueSpy.mock.callCount(), sentBefore);
        assert.equal(createOtpSpy.mock.callCount(), MAX_INVITES_PER_WINDOW);

        await service.register(inviteInput('someone-else@example.com'), 'super-user-1');
        assert.equal(queueSpy.mock.callCount(), sentBefore + 1);
    } finally {
        console.error = originalConsoleError;
        redis.restore();
        txSpy.mock.restore();
        findUserSpy.mock.restore();
        createUserSpy.mock.restore();
        destroyOtpSpy.mock.restore();
        createOtpSpy.mock.restore();
        queueSpy.mock.restore();
    }
});

test('register: fails closed with 503 when the rate-limit store is unavailable', async () => {
    const evalSpy = mock.method(Redis.prototype, 'eval', async () => {
        throw new Error('connection refused');
    });
    const txSpy = withTransactionMock();
    const queueSpy = mock.method(
        messageModule.userAuthenticationQueue,
        'add',
        async () => undefined as never,
    );
    const originalConsoleError = console.error;
    console.error = () => {};

    try {
        await assert.rejects(
            () => service.register(inviteInput('a@example.com'), 'super-user-1'),
            (err: unknown) => err instanceof HttpError && err.statusCode === 503,
        );
        assert.equal(txSpy.mock.callCount(), 0);
        assert.equal(queueSpy.mock.callCount(), 0);
    } finally {
        console.error = originalConsoleError;
        evalSpy.mock.restore();
        txSpy.mock.restore();
        queueSpy.mock.restore();
    }
});

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

const { sequelize } = await import('@/db/sequalize');
const { InvitedUser, UserOTP } = await import('@/db');
const messageModule = await import('@/messaging/producer/user-created.message');
const { InvitedUserService } = await import('@/service/invited-users/inviter-user.crud.service');

const withTransactionMock = () =>
    mock.method(sequelize, 'transaction', async (cb: (t: object) => unknown) => cb({}));

const service = new InvitedUserService();

test('register: createOTP dedupes any prior register-purpose OTP before minting a new one', async () => {
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
        txSpy.mock.restore();
        findUserSpy.mock.restore();
        createUserSpy.mock.restore();
        destroyOtpSpy.mock.restore();
        createOtpSpy.mock.restore();
        queueSpy.mock.restore();
    }
});

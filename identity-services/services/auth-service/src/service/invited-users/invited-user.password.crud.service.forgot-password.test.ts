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
const { PasswordService } =
    await import('@/service/invited-users/invited-user.password.crud.service');
const { userAuthenticationQueue } = await import('@/messaging/producer/user-created.message');

const withTransactionMock = () =>
    mock.method(sequelize, 'transaction', async (cb: (t: object) => unknown) => cb({}));

const passwordService = new PasswordService();

test('requestPasswordReset resolves (never throws) for an unknown email, and queues nothing', async () => {
    const txSpy = withTransactionMock();
    const findUserSpy = mock.method(InvitedUser, 'findOne', async () => null);
    const queueSpy = mock.method(userAuthenticationQueue, 'add', async () => undefined as never);

    try {
        const result = await passwordService.requestPasswordReset({ email: 'ghost@example.com' });
        assert.equal(result, undefined);
        assert.equal(queueSpy.mock.callCount(), 0);
    } finally {
        txSpy.mock.restore();
        findUserSpy.mock.restore();
        queueSpy.mock.restore();
    }
});

test('requestPasswordReset resolves (never throws) for an account with no infra', async () => {
    const txSpy = withTransactionMock();
    const findUserSpy = mock.method(InvitedUser, 'findOne', async () => ({
        id: 'user-1',
        email: 'no-infra@example.com',
        infra_id: [],
        user_name: 'no-infra',
    }));
    const queueSpy = mock.method(userAuthenticationQueue, 'add', async () => undefined as never);

    try {
        const result = await passwordService.requestPasswordReset({
            email: 'no-infra@example.com',
        });
        assert.equal(result, undefined);
        assert.equal(queueSpy.mock.callCount(), 0);
    } finally {
        txSpy.mock.restore();
        findUserSpy.mock.restore();
        queueSpy.mock.restore();
    }
});

test('requestPasswordReset never returns the OTP for a known email — the queued email is the only channel', async () => {
    const txSpy = withTransactionMock();
    const findUserSpy = mock.method(InvitedUser, 'findOne', async () => ({
        id: 'user-1',
        email: 'known@example.com',
        infra_id: ['infra-1'],
        user_name: 'known',
    }));
    const createOtpSpy = mock.method(UserOTP, 'create', async () => ({ otp: '123456' }));
    let queuedOtp: string | undefined;
    const queueSpy = mock.method(
        userAuthenticationQueue,
        'add',
        async (_name: string, data: { otp: string }) => {
            queuedOtp = data.otp;
            return undefined as never;
        },
    );

    try {
        const result = await passwordService.requestPasswordReset({ email: 'known@example.com' });
        assert.equal(result, undefined);
        assert.equal(queueSpy.mock.callCount(), 1);
        // The OTP exists (it was queued for email delivery) but the method's return
        // value — what the HTTP layer could ever see — never carries it.
        assert.equal(typeof queuedOtp, 'string');
    } finally {
        txSpy.mock.restore();
        findUserSpy.mock.restore();
        createOtpSpy.mock.restore();
        queueSpy.mock.restore();
    }
});

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
const { InvitedUser, UserOTP, RefreshToken } = await import('@/db');
const { InvitedUserAuthService } =
    await import('@/service/invited-users/invited-user.auth.service');
const { hashPassword } = await import('@/utils/handle-password');
const { HttpError } = await import('@launchpad/common');

const withTransactionMock = () =>
    mock.method(sequelize, 'transaction', async (cb: (t: object) => unknown) => cb({}));

const service = new InvitedUserAuthService();

test('credentials sign-in: an outstanding password-reset OTP does not block a normal password sign-in', async () => {
    const txSpy = withTransactionMock();
    const passwordHash = await hashPassword('secret123');
    const findUserSpy = mock.method(InvitedUser, 'findOne', async () => ({
        id: 'user-1',
        email: 'a@example.com',
        user_name: 'a',
        role: 'user',
        roles: {},
        infra_id: ['infra-1'],
        password_hash: passwordHash,
        created_at: new Date(),
    }));
    let otpQuery: Record<string, unknown> | undefined;
    const findOtpSpy = mock.method(UserOTP, 'findOne', async (opts: { where: object }) => {
        otpQuery = opts.where as Record<string, unknown>;
        return null; // simulates: only a password-reset-purpose row is live, filtered out
    });
    const refreshSpy = mock.method(
        RefreshToken,
        'create',
        async () => ({ token_id: 'tok-1' }) as unknown,
    );

    try {
        const result = await service.login({ email: 'a@example.com', password: 'secret123' });
        assert.ok(result.accessToken);
        assert.equal(otpQuery?.purpose, 'register');
    } finally {
        txSpy.mock.restore();
        findUserSpy.mock.restore();
        findOtpSpy.mock.restore();
        refreshSpy.mock.restore();
    }
});

test('credentials sign-in: an outstanding registration OTP still blocks sign-in', async () => {
    const txSpy = withTransactionMock();
    const passwordHash = await hashPassword('secret123');
    const findUserSpy = mock.method(InvitedUser, 'findOne', async () => ({
        id: 'user-1',
        email: 'a@example.com',
        password_hash: passwordHash,
    }));
    const findOtpSpy = mock.method(UserOTP, 'findOne', async () => ({ id: 'otp-1' }));

    try {
        await assert.rejects(
            () => service.login({ email: 'a@example.com', password: 'secret123' }),
            (error: unknown) => error instanceof HttpError && error.statusCode === 401,
        );
    } finally {
        txSpy.mock.restore();
        findUserSpy.mock.restore();
        findOtpSpy.mock.restore();
    }
});

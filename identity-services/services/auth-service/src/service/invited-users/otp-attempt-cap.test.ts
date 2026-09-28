import { test, mock } from 'node:test';
import assert from 'node:assert/strict';

// env.ts validates process.env at import time via zod — dummy values only, never real
// secrets, and must be set before the first (dynamic) import of anything that pulls it in.
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

// This test drives the real service layer (not the facade) so it exercises the actual
// attempt-cap wiring around the OTP lookup, mocking only the Sequelize boundary
// (transaction + model statics) and otpAttempts — never a live Postgres/Redis.
const { sequelize } = await import('@/db/sequalize');
const { InvitedUser, UserOTP, RefreshToken } = await import('@/db');
const { otpAttempts } = await import('@/utils/otp-attempts');
const { InvitedUserAuthService } =
    await import('@/service/invited-users/invited-user.auth.service');
const { PasswordService } =
    await import('@/service/invited-users/invited-user.password.crud.service');
const { HttpError } = await import('@launchpad/common');

const fakeUser = {
    id: 'user-1',
    email: 'a@example.com',
    user_name: 'a',
    role: 'user',
    roles: {},
    infra_id: ['infra-1'],
    created_at: new Date(),
    profile_url: undefined,
    is_authenticated: false,
    async save() {
        return this;
    },
};

const withTransactionMock = () =>
    mock.method(sequelize, 'transaction', async (cb: (t: object) => unknown) => cb({}));

const authService = new InvitedUserAuthService();
const passwordService = new PasswordService();

test('authenticateWithOTP: cap already exhausted invalidates the OTP and rejects before checking the guess', async () => {
    const txSpy = withTransactionMock();
    const findUserSpy = mock.method(InvitedUser, 'findOne', async () => ({ ...fakeUser }));
    const hasAttemptsSpy = mock.method(otpAttempts, 'hasAttemptsRemaining', async () => false);
    const destroySpy = mock.method(UserOTP, 'destroy', async () => 1);
    const findOtpSpy = mock.method(UserOTP, 'findOne', async () => {
        throw new Error('must not evaluate the guess once the cap is hit');
    });

    try {
        await assert.rejects(
            () => authService.authenticateWithOTP({ email: 'a@example.com', otp: '000000' }),
            (error: unknown) => error instanceof HttpError && error.statusCode === 429,
        );
        assert.equal(hasAttemptsSpy.mock.callCount(), 1);
        assert.deepEqual(hasAttemptsSpy.mock.calls[0].arguments, ['register', 'a@example.com']);
        assert.equal(destroySpy.mock.callCount(), 1);
        assert.equal(findOtpSpy.mock.callCount(), 0);
    } finally {
        txSpy.mock.restore();
        findUserSpy.mock.restore();
        hasAttemptsSpy.mock.restore();
        destroySpy.mock.restore();
        findOtpSpy.mock.restore();
    }
});

test('authenticateWithOTP: a wrong guess records a failed attempt and does not consume the cap check twice', async () => {
    const txSpy = withTransactionMock();
    const findUserSpy = mock.method(InvitedUser, 'findOne', async () => ({ ...fakeUser }));
    const hasAttemptsSpy = mock.method(otpAttempts, 'hasAttemptsRemaining', async () => true);
    const recordSpy = mock.method(otpAttempts, 'recordFailedAttempt', async () => undefined);
    const findOtpSpy = mock.method(UserOTP, 'findOne', async () => null);

    try {
        await assert.rejects(
            () => authService.authenticateWithOTP({ email: 'a@example.com', otp: 'wrong0' }),
            (error: unknown) => error instanceof HttpError && error.statusCode === 400,
        );
        assert.equal(recordSpy.mock.callCount(), 1);
        assert.deepEqual(recordSpy.mock.calls[0].arguments, ['register', 'a@example.com']);
    } finally {
        txSpy.mock.restore();
        findUserSpy.mock.restore();
        hasAttemptsSpy.mock.restore();
        recordSpy.mock.restore();
        findOtpSpy.mock.restore();
    }
});

test('authenticateWithOTP: a correct guess clears the attempt counter', async () => {
    const txSpy = withTransactionMock();
    const findUserSpy = mock.method(InvitedUser, 'findOne', async () => ({ ...fakeUser }));
    const hasAttemptsSpy = mock.method(otpAttempts, 'hasAttemptsRemaining', async () => true);
    const clearSpy = mock.method(otpAttempts, 'clearAttempts', async () => undefined);
    const otpRecord = { async destroy() {} };
    const findOtpSpy = mock.method(UserOTP, 'findOne', async () => otpRecord);
    const refreshSpy = mock.method(
        RefreshToken,
        'create',
        async () => ({ token_id: 'tok-1' }) as unknown,
    );

    try {
        const result = await authService.authenticateWithOTP({
            email: 'a@example.com',
            otp: '123456',
        });
        assert.ok(result.accessToken);
        assert.equal(clearSpy.mock.callCount(), 1);
        assert.deepEqual(clearSpy.mock.calls[0].arguments, ['register', 'a@example.com']);
    } finally {
        txSpy.mock.restore();
        findUserSpy.mock.restore();
        hasAttemptsSpy.mock.restore();
        clearSpy.mock.restore();
        findOtpSpy.mock.restore();
        refreshSpy.mock.restore();
    }
});

test('verifyResetOTP: cap already exhausted invalidates the OTP and rejects before checking the guess', async () => {
    const txSpy = withTransactionMock();
    const findUserSpy = mock.method(InvitedUser, 'findOne', async () => ({ ...fakeUser }));
    const hasAttemptsSpy = mock.method(otpAttempts, 'hasAttemptsRemaining', async () => false);
    const destroySpy = mock.method(UserOTP, 'destroy', async () => 1);
    const findOtpSpy = mock.method(UserOTP, 'findOne', async () => {
        throw new Error('must not evaluate the guess once the cap is hit');
    });

    try {
        await assert.rejects(
            () => passwordService.verifyResetOTP({ email: 'a@example.com', otp: '000000' }),
            (error: unknown) => error instanceof HttpError && error.statusCode === 429,
        );
        assert.deepEqual(hasAttemptsSpy.mock.calls[0].arguments, [
            'password-reset',
            'a@example.com',
        ]);
        assert.equal(destroySpy.mock.callCount(), 1);
        assert.equal(findOtpSpy.mock.callCount(), 0);
    } finally {
        txSpy.mock.restore();
        findUserSpy.mock.restore();
        hasAttemptsSpy.mock.restore();
        destroySpy.mock.restore();
        findOtpSpy.mock.restore();
    }
});

test('verifyResetOTP: a wrong guess records a failed attempt', async () => {
    const txSpy = withTransactionMock();
    const findUserSpy = mock.method(InvitedUser, 'findOne', async () => ({ ...fakeUser }));
    const hasAttemptsSpy = mock.method(otpAttempts, 'hasAttemptsRemaining', async () => true);
    const recordSpy = mock.method(otpAttempts, 'recordFailedAttempt', async () => undefined);
    const findOtpSpy = mock.method(UserOTP, 'findOne', async () => null);

    try {
        await assert.rejects(
            () => passwordService.verifyResetOTP({ email: 'a@example.com', otp: 'wrong0' }),
            (error: unknown) => error instanceof HttpError && error.statusCode === 400,
        );
        assert.deepEqual(recordSpy.mock.calls[0].arguments, ['password-reset', 'a@example.com']);
    } finally {
        txSpy.mock.restore();
        findUserSpy.mock.restore();
        hasAttemptsSpy.mock.restore();
        recordSpy.mock.restore();
        findOtpSpy.mock.restore();
    }
});

test('verifyResetOTP: a correct guess clears the attempt counter and mints a password_reset-scoped token', async () => {
    const txSpy = withTransactionMock();
    const findUserSpy = mock.method(InvitedUser, 'findOne', async () => ({ ...fakeUser }));
    const hasAttemptsSpy = mock.method(otpAttempts, 'hasAttemptsRemaining', async () => true);
    const clearSpy = mock.method(otpAttempts, 'clearAttempts', async () => undefined);
    const otpRecord = { async destroy() {} };
    const findOtpSpy = mock.method(UserOTP, 'findOne', async () => otpRecord);

    try {
        const { verifyAccessToken } = await import('@/utils/handle-token');
        const token = await passwordService.verifyResetOTP({
            email: 'a@example.com',
            otp: '123456',
        });
        assert.equal(verifyAccessToken(token).scope, 'password_reset');
        assert.equal(clearSpy.mock.callCount(), 1);
        assert.deepEqual(clearSpy.mock.calls[0].arguments, ['password-reset', 'a@example.com']);
    } finally {
        txSpy.mock.restore();
        findUserSpy.mock.restore();
        hasAttemptsSpy.mock.restore();
        clearSpy.mock.restore();
        findOtpSpy.mock.restore();
    }
});

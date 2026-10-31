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

const { sequelize } = await import('@/db/sequalize');
const { InvitedUser, UserOTP, RefreshToken } = await import('@/db');
const { InvitedUserAuthService } =
    await import('@/service/invited-users/invited-user.auth.service');
const { hashPassword } = await import('@/utils/handle-password');
const { HttpError } = await import('@launchpad/common');

const withTransactionMock = () =>
    mock.method(sequelize, 'transaction', async (cb: (t: object) => unknown) => cb({}));

// This whole file runs with ORGS_ENABLED unset (default off) — login()'s
// email_verified_at gate is NOT behind that flag (it's a security fix, not an org
// feature; see R1), so every test here also demonstrates "flag off: behavior matches
// main for a verified account, and the deliberate never-verified-after-OTP-expiry
// closure still applies."
const service = new InvitedUserAuthService();

test('credentials sign-in: a verified account can log in (UserOTP is never consulted)', async () => {
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
        email_verified_at: new Date(),
    }));
    // R1: login() no longer queries UserOTP at all — the old pending-OTP check is gone
    // (it was the SAME lockout bug from the other direction, see below). Throwing from
    // this mock proves login() never touches it.
    const findOtpSpy = mock.method(UserOTP, 'findOne', async () => {
        throw new Error('login() must never query UserOTP');
    });
    const refreshSpy = mock.method(
        RefreshToken,
        'create',
        async () => ({ token_id: 'tok-1' }) as unknown,
    );

    try {
        const result = await service.login({ email: 'a@example.com', password: 'secret123' });
        assert.ok(result.accessToken);
    } finally {
        txSpy.mock.restore();
        findUserSpy.mock.restore();
        findOtpSpy.mock.restore();
        refreshSpy.mock.restore();
    }
});

// Security review R1: register() resets is_authenticated to false on every subsequent
// infra invite (unchanged semantics) — but email_verified_at, set once at the first
// OTP verification, is never cleared again. A verified person invited to a second
// infra (with no OTP-resend route if that new invite's code is ever lost/expired) must
// still be able to log in with their existing password.
test('R1: a verified user invited to a second infra (is_authenticated reset to false) can still log in', async () => {
    const txSpy = withTransactionMock();
    const passwordHash = await hashPassword('secret123');
    const findUserSpy = mock.method(InvitedUser, 'findOne', async () => ({
        id: 'user-1',
        email: 'a@example.com',
        user_name: 'a',
        role: 'user',
        roles: {},
        infra_id: ['infra-1', 'infra-2'],
        password_hash: passwordHash,
        created_at: new Date(),
        // Reset by register() for the infra-2 invite — unchanged, pre-existing
        // behavior this fix must not disturb.
        is_authenticated: false,
        // Set once, the first time this email was proven (verifying infra-1's invite)
        // — never cleared by the infra-2 invite above.
        email_verified_at: new Date('2024-01-01T00:00:00Z'),
    }));
    const findOtpSpy = mock.method(UserOTP, 'findOne', async () => {
        throw new Error('login() must never query UserOTP');
    });
    const refreshSpy = mock.method(
        RefreshToken,
        'create',
        async () => ({ token_id: 'tok-1' }) as unknown,
    );

    try {
        const result = await service.login({ email: 'a@example.com', password: 'secret123' });
        assert.ok(result.accessToken);
    } finally {
        txSpy.mock.restore();
        findUserSpy.mock.restore();
        findOtpSpy.mock.restore();
        refreshSpy.mock.restore();
    }
});

// Security review H1(a): the ONLY thing that used to block this login was the pending,
// non-expired register-purpose OTP row. Once that row's 10-minute TTL passes (or it's
// otherwise gone), `UserOTP.findOne` with `expires_at: {[Op.gt]: now}` finds nothing —
// so before this fix, login would have proceeded for an account that never completed
// verification. is_authenticated: false must block regardless of OTP state.
test('H1(a): login is blocked for an unverified account even once its registration OTP has expired/is gone', async () => {
    const txSpy = withTransactionMock();
    const passwordHash = await hashPassword('secret123');
    const findUserSpy = mock.method(InvitedUser, 'findOne', async () => ({
        id: 'attacker-invited-row',
        email: 'victim@example.com',
        user_name: 'attacker-chosen-name',
        password_hash: passwordHash,
        is_authenticated: false,
    }));
    // No pending OTP at all — simulates the 10-minute TTL having already passed.
    const findOtpSpy = mock.method(UserOTP, 'findOne', async () => null);
    const refreshSpy = mock.method(RefreshToken, 'create', async () => {
        throw new Error('must never mint a token for an unverified account');
    });

    try {
        await assert.rejects(
            () => service.login({ email: 'victim@example.com', password: 'secret123' }),
            (error: unknown) => error instanceof HttpError && error.statusCode === 401,
        );
    } finally {
        txSpy.mock.restore();
        findUserSpy.mock.restore();
        findOtpSpy.mock.restore();
        refreshSpy.mock.restore();
    }
});

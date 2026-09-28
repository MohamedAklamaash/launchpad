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
// (transaction + otpAttempts.claimAttempt's underlying query) — never a live Postgres.
// The concurrency test near the bottom mocks sequelize.query directly instead of
// otpAttempts, to exercise the real atomic-claim logic under Promise.all.
const { sequelize } = await import('@/db/sequalize');
const { InvitedUser, UserOTP, RefreshToken } = await import('@/db');
const { otpAttempts, MAX_OTP_ATTEMPTS, NIL_INVITED_USER_ID } = await import('@/utils/otp-attempts');
const { InvitedUserAuthService } =
    await import('@/service/invited-users/invited-user.auth.service');
const { PasswordService } =
    await import('@/service/invited-users/invited-user.password.crud.service');
const { HttpError } = await import('@launchpad/common');

const fakeClaim = {
    userId: 'user-1',
    email: 'a@example.com',
    user_name: 'a',
    role: 'user',
    roles: {},
    infra_id: ['infra-1'],
    created_at: new Date(),
    attempts: 1,
};

const withTransactionMock = () =>
    mock.method(sequelize, 'transaction', async (cb: (t: object) => unknown) => cb({}));

const authService = new InvitedUserAuthService();
const passwordService = new PasswordService();

test('authenticateWithOTP: cap already exhausted invalidates the OTP (outside any transaction) and rejects before checking the guess', async () => {
    const txSpy = withTransactionMock();
    const claimSpy = mock.method(otpAttempts, 'claimAttempt', async () => ({
        ...fakeClaim,
        attempts: MAX_OTP_ATTEMPTS + 1,
    }));
    const destroySpy = mock.method(UserOTP, 'destroy', async () => 1);
    const findOtpSpy = mock.method(UserOTP, 'findOne', async () => {
        throw new Error('must not evaluate the guess once the cap is hit');
    });

    try {
        await assert.rejects(
            () => authService.authenticateWithOTP({ email: 'a@example.com', otp: '000000' }),
            (error: unknown) => error instanceof HttpError && error.statusCode === 429,
        );
        assert.equal(claimSpy.mock.callCount(), 1);
        assert.equal(findOtpSpy.mock.callCount(), 0);

        // B2: the invalidating destroy must not be part of the transaction that the 429
        // (thrown right after, in the same code path) would otherwise roll back — that
        // only holds if this call never received a `transaction` option at all.
        assert.equal(destroySpy.mock.callCount(), 1);
        const destroyArgs = destroySpy.mock.calls[0].arguments[0] as Record<string, unknown>;
        assert.equal('transaction' in destroyArgs, false);
        assert.deepEqual(destroyArgs.where, { invited_user_id: 'user-1', purpose: 'register' });
    } finally {
        txSpy.mock.restore();
        claimSpy.mock.restore();
        destroySpy.mock.restore();
        findOtpSpy.mock.restore();
    }
});

test('authenticateWithOTP: an unknown email still claims an attempt and gets the same 400 as a wrong guess (no enumeration)', async () => {
    let claimedEmail: string | undefined;
    const claimSpy = mock.method(otpAttempts, 'claimAttempt', async (email: string) => {
        claimedEmail = email;
        return null;
    });
    let otpLookupUserId: string | undefined;
    const findOtpSpy = mock.method(
        UserOTP,
        'findOne',
        async (opts: { where: { invited_user_id: string } }) => {
            otpLookupUserId = opts.where.invited_user_id;
            return null;
        },
    );
    const txSpy = withTransactionMock();

    try {
        await assert.rejects(
            () => authService.authenticateWithOTP({ email: 'ghost@example.com', otp: '000000' }),
            (error: unknown) => error instanceof HttpError && error.statusCode === 400,
        );
        assert.equal(claimSpy.mock.callCount(), 1);
        assert.equal(claimedEmail, 'ghost@example.com');
        // H7 (R3 follow-up): still runs the OTP lookup, bound to a row that can never
        // exist, rather than short-circuiting before ever touching UserOTP.
        assert.equal(findOtpSpy.mock.callCount(), 1);
        assert.equal(otpLookupUserId, NIL_INVITED_USER_ID);
    } finally {
        txSpy.mock.restore();
        claimSpy.mock.restore();
        findOtpSpy.mock.restore();
    }
});

test('authenticateWithOTP: an unknown email and a known-email wrong guess run identical DB operations', async () => {
    // H7 item 3: response timing must not reveal account existence. This asserts the
    // observable proxy for that — the same sequence of DB calls (claimAttempt, then one
    // transaction, then one UserOTP.findOne) runs either way, not one short-circuiting
    // before the other.
    const calls: string[] = [];

    const runWith = async (claimReturn: typeof fakeClaim | null) => {
        calls.length = 0;
        const txSpy = mock.method(sequelize, 'transaction', async (cb: (t: object) => unknown) => {
            calls.push('transaction');
            return cb({});
        });
        const claimSpy = mock.method(otpAttempts, 'claimAttempt', async () => {
            calls.push('claimAttempt');
            return claimReturn;
        });
        const findOtpSpy = mock.method(UserOTP, 'findOne', async () => {
            calls.push('UserOTP.findOne');
            return null;
        });

        try {
            await assert.rejects(
                () => authService.authenticateWithOTP({ email: 'x@example.com', otp: '000000' }),
                (error: unknown) => error instanceof HttpError && error.statusCode === 400,
            );
        } finally {
            txSpy.mock.restore();
            claimSpy.mock.restore();
            findOtpSpy.mock.restore();
        }
        return [...calls];
    };

    const unknownEmailCalls = await runWith(null);
    const knownEmailWrongGuessCalls = await runWith({ ...fakeClaim, attempts: 1 });

    assert.deepEqual(unknownEmailCalls, ['claimAttempt', 'transaction', 'UserOTP.findOne']);
    assert.deepEqual(knownEmailWrongGuessCalls, ['claimAttempt', 'transaction', 'UserOTP.findOne']);
});

test('authenticateWithOTP: a wrong guess is rejected and only matches the register purpose', async () => {
    const txSpy = withTransactionMock();
    const claimSpy = mock.method(otpAttempts, 'claimAttempt', async () => ({ ...fakeClaim }));
    let otpQuery: Record<string, unknown> | undefined;
    const findOtpSpy = mock.method(UserOTP, 'findOne', async (opts: { where: object }) => {
        otpQuery = opts.where as Record<string, unknown>;
        return null;
    });

    try {
        await assert.rejects(
            () => authService.authenticateWithOTP({ email: 'a@example.com', otp: 'wrong0' }),
            (error: unknown) => error instanceof HttpError && error.statusCode === 400,
        );
        assert.equal(claimSpy.mock.callCount(), 1);
        assert.equal(otpQuery?.purpose, 'register');
        assert.equal(otpQuery?.invited_user_id, 'user-1');
    } finally {
        txSpy.mock.restore();
        claimSpy.mock.restore();
        findOtpSpy.mock.restore();
    }
});

test('authenticateWithOTP: a correct guess clears the attempt counter and authenticates the user', async () => {
    const txSpy = withTransactionMock();
    const claimSpy = mock.method(otpAttempts, 'claimAttempt', async () => ({ ...fakeClaim }));
    const resetSpy = mock.method(otpAttempts, 'resetAttempts', async () => undefined);
    const otpRecord = { async destroy() {} };
    const findOtpSpy = mock.method(UserOTP, 'findOne', async () => otpRecord);
    const updateSpy = mock.method(InvitedUser, 'update', async () => [1]);
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
        assert.equal(resetSpy.mock.callCount(), 1);
        assert.equal(resetSpy.mock.calls[0].arguments[0], 'user-1');
        assert.deepEqual(updateSpy.mock.calls[0].arguments[0], { is_authenticated: true });
    } finally {
        txSpy.mock.restore();
        claimSpy.mock.restore();
        resetSpy.mock.restore();
        findOtpSpy.mock.restore();
        updateSpy.mock.restore();
        refreshSpy.mock.restore();
    }
});

test('authenticateWithOTP: only MAX_OTP_ATTEMPTS of 6 concurrent guesses ever reach the OTP lookup (B1 — no check-then-act race)', async () => {
    // Models the real atomic UPDATE ... RETURNING against an in-memory row instead of
    // Postgres — exercises otpAttempts's real claimAttempt logic (not a mock of it)
    // under concurrent calls, the same way the pre-H7 version drove Redis's INCR script.
    const row = { ...fakeClaim, attempts: 0 };
    const querySpy = mock.method(sequelize, 'query', async (sql: string) => {
        if (sql.includes('RETURNING')) {
            row.attempts += 1;
            return [{ ...row, id: row.userId, failed_otp_attempts: row.attempts }];
        }
        return [];
    });

    const txSpy = withTransactionMock();
    let otpLookups = 0;
    const findOtpSpy = mock.method(UserOTP, 'findOne', async () => {
        otpLookups += 1;
        return null; // every guess is wrong — only the count of attempts reaching here matters
    });
    const destroySpy = mock.method(UserOTP, 'destroy', async () => 1);

    try {
        const results = await Promise.allSettled(
            Array.from({ length: 6 }, () =>
                authService.authenticateWithOTP({ email: 'a@example.com', otp: '000000' }),
            ),
        );

        const statusCodes = results.map((r) =>
            r.status === 'rejected' && r.reason instanceof HttpError ? r.reason.statusCode : null,
        );
        assert.equal(statusCodes.filter((c) => c === 400).length, MAX_OTP_ATTEMPTS);
        assert.equal(statusCodes.filter((c) => c === 429).length, 1);
        assert.equal(otpLookups, MAX_OTP_ATTEMPTS);
        assert.equal(destroySpy.mock.callCount(), 1);
    } finally {
        querySpy.mock.restore();
        txSpy.mock.restore();
        findOtpSpy.mock.restore();
        destroySpy.mock.restore();
    }
});

test('verifyResetOTP: cap already exhausted invalidates the OTP (outside any transaction) and rejects before checking the guess', async () => {
    const txSpy = withTransactionMock();
    const claimSpy = mock.method(otpAttempts, 'claimAttempt', async () => ({
        ...fakeClaim,
        attempts: MAX_OTP_ATTEMPTS + 1,
    }));
    const destroySpy = mock.method(UserOTP, 'destroy', async () => 1);
    const findOtpSpy = mock.method(UserOTP, 'findOne', async () => {
        throw new Error('must not evaluate the guess once the cap is hit');
    });

    try {
        await assert.rejects(
            () => passwordService.verifyResetOTP({ email: 'a@example.com', otp: '000000' }),
            (error: unknown) => error instanceof HttpError && error.statusCode === 429,
        );
        assert.equal(findOtpSpy.mock.callCount(), 0);
        assert.equal(destroySpy.mock.callCount(), 1);
        const destroyArgs = destroySpy.mock.calls[0].arguments[0] as Record<string, unknown>;
        assert.equal('transaction' in destroyArgs, false);
        assert.deepEqual(destroyArgs.where, {
            invited_user_id: 'user-1',
            purpose: 'password-reset',
        });
    } finally {
        txSpy.mock.restore();
        claimSpy.mock.restore();
        destroySpy.mock.restore();
        findOtpSpy.mock.restore();
    }
});

test('verifyResetOTP: an unknown email still claims an attempt and gets the same 400 as a wrong guess', async () => {
    const claimSpy = mock.method(otpAttempts, 'claimAttempt', async () => null);
    const findOtpSpy = mock.method(
        UserOTP,
        'findOne',
        async (opts: { where: { invited_user_id: string } }) => {
            assert.equal(opts.where.invited_user_id, NIL_INVITED_USER_ID);
            return null;
        },
    );
    const txSpy = withTransactionMock();

    try {
        await assert.rejects(
            () => passwordService.verifyResetOTP({ email: 'ghost@example.com', otp: '000000' }),
            (error: unknown) => error instanceof HttpError && error.statusCode === 400,
        );
        assert.equal(findOtpSpy.mock.callCount(), 1);
    } finally {
        txSpy.mock.restore();
        claimSpy.mock.restore();
        findOtpSpy.mock.restore();
    }
});

test('verifyResetOTP: a wrong guess is rejected and only matches the password-reset purpose', async () => {
    const txSpy = withTransactionMock();
    const claimSpy = mock.method(otpAttempts, 'claimAttempt', async () => ({ ...fakeClaim }));
    let otpQuery: Record<string, unknown> | undefined;
    const findOtpSpy = mock.method(UserOTP, 'findOne', async (opts: { where: object }) => {
        otpQuery = opts.where as Record<string, unknown>;
        return null;
    });

    try {
        await assert.rejects(
            () => passwordService.verifyResetOTP({ email: 'a@example.com', otp: 'wrong0' }),
            (error: unknown) => error instanceof HttpError && error.statusCode === 400,
        );
        assert.equal(otpQuery?.purpose, 'password-reset');
    } finally {
        txSpy.mock.restore();
        claimSpy.mock.restore();
        findOtpSpy.mock.restore();
    }
});

test('verifyResetOTP: a register-purpose OTP cannot be redeemed through this endpoint', async () => {
    // B3's cross-purpose bug: without a purpose filter, any live code for this user —
    // regardless of which flow minted it — would match here.
    const txSpy = withTransactionMock();
    const claimSpy = mock.method(otpAttempts, 'claimAttempt', async () => ({ ...fakeClaim }));
    const findOtpSpy = mock.method(
        UserOTP,
        'findOne',
        async (opts: { where: { purpose: string } }) =>
            opts.where.purpose === 'password-reset' ? null : { async destroy() {} },
    );

    try {
        await assert.rejects(
            () => passwordService.verifyResetOTP({ email: 'a@example.com', otp: '123456' }),
            (error: unknown) => error instanceof HttpError && error.statusCode === 400,
        );
    } finally {
        txSpy.mock.restore();
        claimSpy.mock.restore();
        findOtpSpy.mock.restore();
    }
});

test('verifyResetOTP: a correct guess clears the attempt counter and mints a password_reset-scoped token', async () => {
    const txSpy = withTransactionMock();
    const claimSpy = mock.method(otpAttempts, 'claimAttempt', async () => ({ ...fakeClaim }));
    const resetSpy = mock.method(otpAttempts, 'resetAttempts', async () => undefined);
    const otpRecord = { async destroy() {} };
    const findOtpSpy = mock.method(UserOTP, 'findOne', async () => otpRecord);

    try {
        const { verifyAccessToken } = await import('@/utils/handle-token');
        const token = await passwordService.verifyResetOTP({
            email: 'a@example.com',
            otp: '123456',
        });
        assert.equal(verifyAccessToken(token).scope, 'password_reset');
        assert.equal(resetSpy.mock.callCount(), 1);
        assert.equal(resetSpy.mock.calls[0].arguments[0], 'user-1');
    } finally {
        txSpy.mock.restore();
        claimSpy.mock.restore();
        resetSpy.mock.restore();
        findOtpSpy.mock.restore();
    }
});

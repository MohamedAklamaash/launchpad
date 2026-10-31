import { test, mock, beforeEach, after } from 'node:test';
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

const { Op } = await import('sequelize');
const { HttpError } = await import('@launchpad/common');
const { sequelize } = await import('@/db/sequalize');
const { InvitedUser, RefreshToken } = await import('@/db');
const { resetInviterChosenPasswords } = await import('@/service/inviter-password-reset.service');
const { InvitedUserAuthService } =
    await import('@/service/invited-users/invited-user.auth.service');
const { comparePassword, hashPassword } = await import('@/utils/handle-password');

type Row = {
    id: string;
    email: string;
    password_hash: string;
    email_verified_at: Date | null;
    password_reset_required_at: Date | null;
};

const rows: Row[] = [];
let sessions: { user_id: string }[] = [];

const isCandidate = (row: Row, where: Record<string, unknown>) => {
    const verified = where.email_verified_at as { [Op.ne]: null; [Op.lt]?: Date };
    const before = verified[Op.lt];
    return (
        row.email_verified_at !== null &&
        (!before || row.email_verified_at < before) &&
        row.password_reset_required_at === null
    );
};

mock.method(sequelize, 'transaction', async (cb: (t: object) => unknown) =>
    cb({ afterCommit: () => undefined }),
);
mock.method(
    InvitedUser,
    'count',
    async (o: { where: Record<string, unknown> }) =>
        rows.filter((r) => isCandidate(r, o.where)).length,
);
mock.method(InvitedUser, 'findAll', async (o: { where: Record<string, unknown> }) =>
    rows.filter((r) => isCandidate(r, o.where)),
);
mock.method(
    InvitedUser,
    'update',
    async (values: Partial<Row>, o: { where: { id: { [Op.in]: string[] } } }) => {
        const hits = rows.filter((r) => o.where.id[Op.in].includes(r.id));
        hits.forEach((r) => Object.assign(r, values));
        return [hits.length];
    },
);
mock.method(RefreshToken, 'destroy', async (o: { where: { user_id: { [Op.in]: string[] } } }) => {
    const ids = o.where.user_id[Op.in];
    const before = sessions.length;
    sessions = sessions.filter((s) => !ids.includes(s.user_id));
    return before - sessions.length;
});
mock.method(
    InvitedUser,
    'findOne',
    async (o: { where: { email: string } }) => rows.find((r) => r.email === o.where.email) ?? null,
);

const inviterHash = await hashPassword('inviter-chosen');

beforeEach(() => {
    rows.length = 0;
    rows.push(
        {
            id: 'verified-old',
            email: 'old@example.com',
            password_hash: inviterHash,
            email_verified_at: new Date('2026-01-01T00:00:00Z'),
            password_reset_required_at: null,
        },
        {
            id: 'verified-new',
            email: 'new@example.com',
            password_hash: inviterHash,
            email_verified_at: new Date('2026-12-01T00:00:00Z'),
            password_reset_required_at: null,
        },
        {
            id: 'unverified',
            email: 'pending@example.com',
            password_hash: inviterHash,
            email_verified_at: null,
            password_reset_required_at: null,
        },
    );
    sessions = [{ user_id: 'verified-old' }, { user_id: 'verified-new' }, { user_id: 'other' }];
});

after(() => mock.restoreAll());

test('dry run prints the candidate count and writes nothing', async () => {
    const result = await resetInviterChosenPasswords({ dryRun: true });

    assert.equal(result.candidates, 2);
    assert.equal(result.reset, 0);
    assert.equal(
        rows.every((r) => r.password_reset_required_at === null),
        true,
    );
    assert.equal(await comparePassword('inviter-chosen', rows[0]!.password_hash), true);
    assert.equal(sessions.length, 3);
});

test('resets every verified row, marks it, revokes its sessions, and leaves unverified rows alone', async () => {
    const result = await resetInviterChosenPasswords({ dryRun: false });

    assert.equal(result.reset, 2);
    assert.equal(result.sessionsRevoked, 2);
    for (const row of rows.filter((r) => r.email_verified_at)) {
        assert.ok(row.password_reset_required_at);
        assert.equal(await comparePassword('inviter-chosen', row.password_hash), false);
    }
    assert.equal(rows[2]!.password_reset_required_at, null);
    assert.equal(await comparePassword('inviter-chosen', rows[2]!.password_hash), true);
    assert.deepEqual(sessions, [{ user_id: 'other' }]);
});

test('is idempotent: a second run resets nothing', async () => {
    await resetInviterChosenPasswords({ dryRun: false });
    const marked = rows.map((r) => r.password_reset_required_at);

    const second = await resetInviterChosenPasswords({ dryRun: false });

    assert.equal(second.reset, 0);
    assert.deepEqual(
        rows.map((r) => r.password_reset_required_at),
        marked,
    );
});

test('verifiedBefore limits the reset to rows verified before the cutoff', async () => {
    const result = await resetInviterChosenPasswords({
        dryRun: false,
        verifiedBefore: new Date('2026-06-01T00:00:00Z'),
    });

    assert.equal(result.reset, 1);
    assert.ok(rows[0]!.password_reset_required_at);
    assert.equal(rows[1]!.password_reset_required_at, null);
});

test('login for a marked row is a clear 401 telling the user to reset their password', async () => {
    await resetInviterChosenPasswords({ dryRun: false });

    await assert.rejects(
        () =>
            new InvitedUserAuthService().login({
                email: 'old@example.com',
                password: 'inviter-chosen',
            }),
        (error: unknown) =>
            error instanceof HttpError && error.statusCode === 401 && /reset/i.test(error.message),
    );
});

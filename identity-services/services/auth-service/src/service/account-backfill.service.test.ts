import { test, mock } from 'node:test';
import assert from 'node:assert/strict';

// env.ts validates process.env at import time via zod — dummy values only, never real
// secrets, and must be set before the first (dynamic) import of anything that pulls it in.
// Same preamble as src/service/invited-users/otp-attempt-cap.test.ts.
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

const { Account, InvitedUser, User } = await import('@/db');
const { backfillAccounts } = await import('@/service/account-backfill.service');
const { accountIdForEmail } = await import('@/utils/account-id');
const { Op } = await import('sequelize');
const { sequelize } = await import('@/db/sequalize');

// Ordered log of lock / holder re-check / link calls, plus a hook that runs right after the
// lock is taken so a test can model another writer claiming the Account before the re-check.
const events: string[] = [];
let afterLock: (() => void) | null = null;

mock.method(sequelize, 'transaction', async (cb: (t: object) => unknown) => cb({}));
mock.method(sequelize, 'query', async (sql: string, opts: { replacements: { key: string } }) => {
    assert.match(sql, /pg_advisory_xact_lock\(hashtext\(:key\)\)/);
    events.push(`lock:${opts.replacements.key}`);
    afterLock?.();
    return [];
});

interface FakeRow {
    id: string;
    email: string;
    user_name: string;
    account_id: string | null;
    email_verified_at?: Date | null;
}

// A tiny in-memory stand-in for the three tables the backfill touches, driving the real
// Sequelize model methods (mocked at the model boundary, never a live Postgres) the same
// way otp-attempt-cap.test.ts mocks UserOTP/RefreshToken.
const makeFakeTable = () => {
    const rows: FakeRow[] = [];
    return {
        rows,
        findAll: async () => rows.map((row) => ({ get: () => ({ ...row }) })),
        // Handles both shapes backfillAccounts issues against this table: the
        // group-linking call (where.id IN [...], account_id: null) and, on
        // InvitedUser specifically, the "unlink stale pre-fix links" call (where:
        // { email_verified_at: null, account_id: { [Op.ne]: null } }, no `id` key).
        findOne: async (opts: { where: { account_id: string } }) => {
            events.push('recheck');
            return rows.find((row) => row.account_id === opts.where.account_id) ?? null;
        },
        update: async (
            values: Record<string, unknown>,
            opts: { where: Record<string, unknown> },
        ) => {
            let affected = 0;
            if ('id' in opts.where) {
                events.push('link');
                const ids = (opts.where.id as { [Op.in]: string[] })[Op.in];
                for (const row of rows) {
                    if (ids.includes(row.id) && row.account_id === null) {
                        row.account_id = values.account_id as string;
                        affected += 1;
                    }
                }
            } else {
                for (const row of rows) {
                    if ((row.email_verified_at ?? null) === null && row.account_id !== null) {
                        row.account_id = null;
                        affected += 1;
                    }
                }
            }
            return [affected];
        },
    };
};

const makeFakeAccounts = () => {
    const rows: { id: string; email: string; display_name: string | null }[] = [];
    return {
        rows,
        findAll: async (opts: { where: { id: { [Op.in]: string[] } } }) => {
            const ids = opts.where.id[Op.in];
            return rows.filter((row) => ids.includes(row.id)).map((row) => ({ id: row.id }));
        },
        // The service pre-filters out already-existing ids via findAll above, so by the
        // time bulkCreate runs here every record is genuinely new — same contract the real
        // Sequelize/Postgres ON CONFLICT DO NOTHING insert has (skip what already exists).
        bulkCreate: async (
            records: { id: string; email: string; display_name: string | null }[],
        ) => {
            for (const record of records) {
                if (!rows.some((r) => r.id === record.id)) {
                    rows.push({ ...record });
                }
            }
        },
    };
};

test('backfillAccounts is idempotent: running it twice yields the same final state and a no-op second pass', async () => {
    const fakeUsers = makeFakeTable();
    const fakeInvitedUsers = makeFakeTable();
    const fakeAccounts = makeFakeAccounts();

    fakeUsers.rows.push(
        { id: 'user-1', email: 'Owner@Example.com', user_name: 'owner', account_id: null },
        { id: 'user-2', email: 'second@example.com', user_name: 'second', account_id: null },
    );
    fakeInvitedUsers.rows.push(
        // Cross-table collision: same normalized email as user-1. Neither row may be
        // linked: merging two credential rows into one Account is a manual decision.
        {
            id: 'invited-1',
            email: '  owner@example.com  ',
            user_name: 'owner-invited',
            account_id: null,
            email_verified_at: new Date(),
        },
        {
            id: 'invited-2',
            email: 'third@example.com',
            user_name: 'third',
            account_id: null,
            email_verified_at: new Date(),
        },
    );

    const findUsersSpy = mock.method(User, 'findAll', fakeUsers.findAll);
    const findInvitedSpy = mock.method(InvitedUser, 'findAll', fakeInvitedUsers.findAll);
    const findAccountsSpy = mock.method(Account, 'findAll', fakeAccounts.findAll);
    const bulkCreateSpy = mock.method(Account, 'bulkCreate', fakeAccounts.bulkCreate);
    const updateUsersSpy = mock.method(User, 'update', fakeUsers.update);
    const updateInvitedSpy = mock.method(InvitedUser, 'update', fakeInvitedUsers.update);
    const findOneUsersSpy = mock.method(User, 'findOne', fakeUsers.findOne);
    const findOneInvitedSpy = mock.method(InvitedUser, 'findOne', fakeInvitedUsers.findOne);

    try {
        const first = await backfillAccounts();

        assert.equal(first.accountsCreated, 2);
        assert.equal(first.usersLinked, 1);
        assert.equal(first.invitedUsersLinked, 1);
        assert.equal(first.collisionGroups, 1);
        assert.equal(first.sharedAccountRowsSkipped, 2);

        assert.equal(fakeUsers.rows[0].account_id, null);
        assert.equal(fakeInvitedUsers.rows[0].account_id, null);
        assert.equal(fakeAccounts.rows.length, 2);

        const snapshotAfterFirst = {
            users: JSON.parse(JSON.stringify(fakeUsers.rows)),
            invitedUsers: JSON.parse(JSON.stringify(fakeInvitedUsers.rows)),
            accounts: JSON.parse(JSON.stringify(fakeAccounts.rows)),
        };

        const second = await backfillAccounts();

        assert.equal(second.accountsCreated, 0);
        assert.equal(second.usersLinked, 0);
        assert.equal(second.invitedUsersLinked, 0);
        assert.equal(second.collisionGroups, 1);
        assert.equal(second.sharedAccountRowsSkipped, 2);
        assert.equal(second.staleLinksUnlinked, 0);

        // Round-tripped through JSON on both sides — snapshotAfterFirst already was,
        // and these rows now carry real Date objects (email_verified_at), which
        // deepEqual would otherwise compare against JSON's string serialization as
        // unequal even when the underlying values match.
        assert.deepEqual(
            JSON.parse(
                JSON.stringify({
                    users: fakeUsers.rows,
                    invitedUsers: fakeInvitedUsers.rows,
                    accounts: fakeAccounts.rows,
                }),
            ),
            snapshotAfterFirst,
        );
    } finally {
        findUsersSpy.mock.restore();
        findInvitedSpy.mock.restore();
        findAccountsSpy.mock.restore();
        bulkCreateSpy.mock.restore();
        updateUsersSpy.mock.restore();
        updateInvitedSpy.mock.restore();
        findOneUsersSpy.mock.restore();
        findOneInvitedSpy.mock.restore();
    }
});

test('backfillAccounts never overwrites a row already linked to a different account_id', async () => {
    // Models a row linked by a later ticket's organic (uuidv7) Account before this script
    // ever ran against it — the account_id: null guard must leave it untouched.
    const fakeUsers = makeFakeTable();
    const fakeInvitedUsers = makeFakeTable();
    const fakeAccounts = makeFakeAccounts();

    const alreadyLinkedId = 'organic-account-id';
    fakeUsers.rows.push({
        id: 'user-1',
        email: 'already@example.com',
        user_name: 'already',
        account_id: alreadyLinkedId,
    });

    const findUsersSpy = mock.method(User, 'findAll', fakeUsers.findAll);
    const findInvitedSpy = mock.method(InvitedUser, 'findAll', fakeInvitedUsers.findAll);
    const findAccountsSpy = mock.method(Account, 'findAll', fakeAccounts.findAll);
    const bulkCreateSpy = mock.method(Account, 'bulkCreate', fakeAccounts.bulkCreate);
    const updateUsersSpy = mock.method(User, 'update', fakeUsers.update);
    const updateInvitedSpy = mock.method(InvitedUser, 'update', fakeInvitedUsers.update);
    const findOneUsersSpy = mock.method(User, 'findOne', fakeUsers.findOne);
    const findOneInvitedSpy = mock.method(InvitedUser, 'findOne', fakeInvitedUsers.findOne);

    try {
        const result = await backfillAccounts();

        assert.equal(result.usersLinked, 0);
        assert.equal(fakeUsers.rows[0].account_id, alreadyLinkedId);
    } finally {
        findUsersSpy.mock.restore();
        findInvitedSpy.mock.restore();
        findAccountsSpy.mock.restore();
        bulkCreateSpy.mock.restore();
        updateUsersSpy.mock.restore();
        updateInvitedSpy.mock.restore();
        findOneUsersSpy.mock.restore();
        findOneInvitedSpy.mock.restore();
    }
});

test('backfillAccounts links neither row of a case-only collision within the same table', async () => {
    // Postgres's unique index on `email` is case-sensitive, so `Foo@x.com` and `foo@x.com`
    // can coexist today within `users` itself (not just across tables) — the normalized-
    // email grouping must catch this one too, and refuse to merge the two rows.
    const fakeUsers = makeFakeTable();
    const fakeInvitedUsers = makeFakeTable();
    const fakeAccounts = makeFakeAccounts();

    fakeUsers.rows.push(
        { id: 'user-1', email: 'Dup@Example.com', user_name: 'dup-1', account_id: null },
        { id: 'user-2', email: 'dup@example.com', user_name: 'dup-2', account_id: null },
    );

    const findUsersSpy = mock.method(User, 'findAll', fakeUsers.findAll);
    const findInvitedSpy = mock.method(InvitedUser, 'findAll', fakeInvitedUsers.findAll);
    const findAccountsSpy = mock.method(Account, 'findAll', fakeAccounts.findAll);
    const bulkCreateSpy = mock.method(Account, 'bulkCreate', fakeAccounts.bulkCreate);
    const updateUsersSpy = mock.method(User, 'update', fakeUsers.update);
    const updateInvitedSpy = mock.method(InvitedUser, 'update', fakeInvitedUsers.update);
    const findOneUsersSpy = mock.method(User, 'findOne', fakeUsers.findOne);
    const findOneInvitedSpy = mock.method(InvitedUser, 'findOne', fakeInvitedUsers.findOne);

    try {
        const result = await backfillAccounts();

        assert.equal(result.accountsCreated, 0);
        assert.equal(result.collisionGroups, 1);
        assert.equal(result.sharedAccountRowsSkipped, 2);
        assert.equal(fakeUsers.rows[0].account_id, null);
        assert.equal(fakeUsers.rows[1].account_id, null);
    } finally {
        findUsersSpy.mock.restore();
        findInvitedSpy.mock.restore();
        findAccountsSpy.mock.restore();
        bulkCreateSpy.mock.restore();
        updateUsersSpy.mock.restore();
        updateInvitedSpy.mock.restore();
        findOneUsersSpy.mock.restore();
        findOneInvitedSpy.mock.restore();
    }
});

// Security review H1(c): an unverified InvitedUser must never be linked to an Account —
// especially not one a real, already-verified identity (GitHub User or a verified
// InvitedUser) owns via a shared email. Linking it would hand the unverified row
// (created by an inviter with an email and password of their own choosing) the victim's
// account_id, and from there any org that account_id belongs to via switch-org.
test("backfillAccounts never links an unverified InvitedUser, even one sharing a verified identity's email", async () => {
    const fakeUsers = makeFakeTable();
    const fakeInvitedUsers = makeFakeTable();
    const fakeAccounts = makeFakeAccounts();

    fakeUsers.rows.push({
        id: 'victim-user',
        email: 'victim@example.com',
        user_name: 'victim',
        account_id: null,
    });
    fakeInvitedUsers.rows.push(
        {
            id: 'attacker-invited-row',
            email: 'victim@example.com',
            user_name: 'attacker-chosen-name',
            account_id: null,
            email_verified_at: null,
        },
        {
            id: 'verified-invited-row',
            email: 'genuinely-verified@example.com',
            user_name: 'verified',
            account_id: null,
            email_verified_at: new Date(),
        },
    );

    const findUsersSpy = mock.method(User, 'findAll', fakeUsers.findAll);
    const findInvitedSpy = mock.method(InvitedUser, 'findAll', fakeInvitedUsers.findAll);
    const findAccountsSpy = mock.method(Account, 'findAll', fakeAccounts.findAll);
    const bulkCreateSpy = mock.method(Account, 'bulkCreate', fakeAccounts.bulkCreate);
    const updateUsersSpy = mock.method(User, 'update', fakeUsers.update);
    const updateInvitedSpy = mock.method(InvitedUser, 'update', fakeInvitedUsers.update);
    const findOneUsersSpy = mock.method(User, 'findOne', fakeUsers.findOne);
    const findOneInvitedSpy = mock.method(InvitedUser, 'findOne', fakeInvitedUsers.findOne);

    try {
        const result = await backfillAccounts();

        // The victim's own User row still links normally.
        assert.equal(fakeUsers.rows[0].account_id, accountIdForEmail('victim@example.com'));
        // The attacker's unverified row is left completely unlinked — it never shares
        // the victim's account_id.
        assert.equal(fakeInvitedUsers.rows[0].account_id, null);
        assert.notEqual(fakeInvitedUsers.rows[0].account_id, fakeUsers.rows[0].account_id);
        // A genuinely verified, non-colliding row still links normally.
        assert.equal(
            fakeInvitedUsers.rows[1].account_id,
            accountIdForEmail('genuinely-verified@example.com'),
        );

        assert.equal(result.usersLinked, 1);
        assert.equal(result.invitedUsersLinked, 1); // only the verified row
        assert.equal(result.unverifiedRowsSkipped, 1);
        // The collision (victim's User row vs. the attacker's unverified InvitedUser
        // row sharing an email) is still detected and reported for manual review, even
        // though nothing was merged.
        assert.equal(result.collisionGroups, 1);
    } finally {
        findUsersSpy.mock.restore();
        findInvitedSpy.mock.restore();
        findAccountsSpy.mock.restore();
        bulkCreateSpy.mock.restore();
        updateUsersSpy.mock.restore();
        updateInvitedSpy.mock.restore();
        findOneUsersSpy.mock.restore();
        findOneInvitedSpy.mock.restore();
    }
});

// Security review R1 follow-up ("unlink pre-fix links"): a row linked under the OLD
// is_authenticated-based rule, then reset to is_authenticated=false by a later infra
// invite before the email_verified_at column/backfill ever reached it, is left holding
// an account_id the new, stricter criterion can't vouch for. It must be unlinked so it
// goes through authenticateWithOTP's verify hook again to get correctly relinked.
test('backfillAccounts unlinks a stale pre-fix account_id on an unverified row', async () => {
    const fakeUsers = makeFakeTable();
    const fakeInvitedUsers = makeFakeTable();
    const fakeAccounts = makeFakeAccounts();

    const staleAccountId = 'stale-pre-fix-account-id';
    fakeInvitedUsers.rows.push({
        id: 'reinvited-row',
        email: 'reinvited@example.com',
        user_name: 'reinvited',
        // Linked by an earlier pass, back when the only signal was is_authenticated.
        account_id: staleAccountId,
        // Reset to false by a later infra invite before this column/backfill ever
        // caught up with it.
        email_verified_at: null,
    });

    const findUsersSpy = mock.method(User, 'findAll', fakeUsers.findAll);
    const findInvitedSpy = mock.method(InvitedUser, 'findAll', fakeInvitedUsers.findAll);
    const findAccountsSpy = mock.method(Account, 'findAll', fakeAccounts.findAll);
    const bulkCreateSpy = mock.method(Account, 'bulkCreate', fakeAccounts.bulkCreate);
    const updateUsersSpy = mock.method(User, 'update', fakeUsers.update);
    const updateInvitedSpy = mock.method(InvitedUser, 'update', fakeInvitedUsers.update);
    const findOneUsersSpy = mock.method(User, 'findOne', fakeUsers.findOne);
    const findOneInvitedSpy = mock.method(InvitedUser, 'findOne', fakeInvitedUsers.findOne);

    try {
        const result = await backfillAccounts();

        assert.equal(fakeInvitedUsers.rows[0].account_id, null);
        assert.equal(result.staleLinksUnlinked, 1);
        // Never re-linked in the same pass — it's still unverified, same as any other
        // unverified row.
        assert.equal(result.invitedUsersLinked, 0);
        assert.equal(result.unverifiedRowsSkipped, 1);
    } finally {
        findUsersSpy.mock.restore();
        findInvitedSpy.mock.restore();
        findAccountsSpy.mock.restore();
        bulkCreateSpy.mock.restore();
        updateUsersSpy.mock.restore();
        updateInvitedSpy.mock.restore();
        findOneUsersSpy.mock.restore();
        findOneInvitedSpy.mock.restore();
    }
});

test('backfillAccounts leaves a verified rows account_id untouched (no stale unlink false-positive)', async () => {
    const fakeUsers = makeFakeTable();
    const fakeInvitedUsers = makeFakeTable();
    const fakeAccounts = makeFakeAccounts();

    const linkedId = accountIdForEmail('verified@example.com');
    fakeInvitedUsers.rows.push({
        id: 'verified-row',
        email: 'verified@example.com',
        user_name: 'verified',
        account_id: linkedId,
        email_verified_at: new Date(),
    });

    const findUsersSpy = mock.method(User, 'findAll', fakeUsers.findAll);
    const findInvitedSpy = mock.method(InvitedUser, 'findAll', fakeInvitedUsers.findAll);
    const findAccountsSpy = mock.method(Account, 'findAll', fakeAccounts.findAll);
    const bulkCreateSpy = mock.method(Account, 'bulkCreate', fakeAccounts.bulkCreate);
    const updateUsersSpy = mock.method(User, 'update', fakeUsers.update);
    const updateInvitedSpy = mock.method(InvitedUser, 'update', fakeInvitedUsers.update);
    const findOneUsersSpy = mock.method(User, 'findOne', fakeUsers.findOne);
    const findOneInvitedSpy = mock.method(InvitedUser, 'findOne', fakeInvitedUsers.findOne);

    try {
        const result = await backfillAccounts();

        assert.equal(fakeInvitedUsers.rows[0].account_id, linkedId);
        assert.equal(result.staleLinksUnlinked, 0);
    } finally {
        findUsersSpy.mock.restore();
        findInvitedSpy.mock.restore();
        findAccountsSpy.mock.restore();
        bulkCreateSpy.mock.restore();
        updateUsersSpy.mock.restore();
        updateInvitedSpy.mock.restore();
        findOneUsersSpy.mock.restore();
        findOneInvitedSpy.mock.restore();
    }
});

test('backfillAccounts never links a verified row into an Account another row already holds, on any run', async () => {
    const fakeUsers = makeFakeTable();
    const fakeInvitedUsers = makeFakeTable();
    const fakeAccounts = makeFakeAccounts();

    const heldId = accountIdForEmail('victim@example.com');
    fakeUsers.rows.push({
        id: 'victim-user',
        email: 'victim@example.com',
        user_name: 'victim',
        account_id: heldId,
    });
    fakeInvitedUsers.rows.push({
        id: 'attacker-invited',
        email: 'Victim@example.com',
        user_name: 'attacker-chosen',
        account_id: null,
        email_verified_at: new Date(),
    });

    const spies = [
        mock.method(User, 'findAll', fakeUsers.findAll),
        mock.method(InvitedUser, 'findAll', fakeInvitedUsers.findAll),
        mock.method(Account, 'findAll', fakeAccounts.findAll),
        mock.method(Account, 'bulkCreate', fakeAccounts.bulkCreate),
        mock.method(User, 'update', fakeUsers.update),
        mock.method(InvitedUser, 'update', fakeInvitedUsers.update),
        mock.method(User, 'findOne', fakeUsers.findOne),
        mock.method(InvitedUser, 'findOne', fakeInvitedUsers.findOne),
    ];

    try {
        for (const run of [1, 2]) {
            const result = await backfillAccounts();
            assert.equal(result.invitedUsersLinked, 0, `run ${run}`);
            assert.equal(result.sharedAccountRowsSkipped, 1, `run ${run}`);
            assert.equal(fakeInvitedUsers.rows[0].account_id, null, `run ${run}`);
            assert.equal(fakeUsers.rows[0].account_id, heldId, `run ${run}`);
        }
    } finally {
        spies.forEach((spy) => spy.mock.restore());
    }
});

test('each link runs under the account-link advisory lock and re-checks holders inside it', async () => {
    const fakeUsers = makeFakeTable();
    const fakeInvitedUsers = makeFakeTable();
    const fakeAccounts = makeFakeAccounts();
    fakeUsers.rows.push({ id: 'u1', email: 'one@example.com', user_name: 'one', account_id: null });
    events.length = 0;

    const spies = [
        mock.method(User, 'findAll', fakeUsers.findAll),
        mock.method(InvitedUser, 'findAll', fakeInvitedUsers.findAll),
        mock.method(Account, 'findAll', fakeAccounts.findAll),
        mock.method(Account, 'bulkCreate', fakeAccounts.bulkCreate),
        mock.method(User, 'update', fakeUsers.update),
        mock.method(InvitedUser, 'update', fakeInvitedUsers.update),
        mock.method(User, 'findOne', fakeUsers.findOne),
        mock.method(InvitedUser, 'findOne', fakeInvitedUsers.findOne),
    ];

    try {
        const result = await backfillAccounts();

        assert.equal(result.usersLinked, 1);
        assert.deepEqual(events, [
            `lock:account-link:${accountIdForEmail('one@example.com')}`,
            'recheck',
            'recheck',
            'link',
        ]);
    } finally {
        spies.forEach((spy) => spy.mock.restore());
    }
});

test('a row that claims the Account after the plan was made but before the lock is not merged into', async () => {
    const fakeUsers = makeFakeTable();
    const fakeInvitedUsers = makeFakeTable();
    const fakeAccounts = makeFakeAccounts();
    const accountId = accountIdForEmail('race@example.com');
    fakeUsers.rows.push({
        id: 'u1',
        email: 'race@example.com',
        user_name: 'one',
        account_id: null,
    });
    afterLock = () => {
        fakeInvitedUsers.rows.push({
            id: 'live-linked',
            email: 'race@example.com',
            user_name: 'two',
            account_id: accountId,
            email_verified_at: new Date(),
        });
    };

    const spies = [
        mock.method(User, 'findAll', fakeUsers.findAll),
        mock.method(InvitedUser, 'findAll', fakeInvitedUsers.findAll),
        mock.method(Account, 'findAll', fakeAccounts.findAll),
        mock.method(Account, 'bulkCreate', fakeAccounts.bulkCreate),
        mock.method(User, 'update', fakeUsers.update),
        mock.method(InvitedUser, 'update', fakeInvitedUsers.update),
        mock.method(User, 'findOne', fakeUsers.findOne),
        mock.method(InvitedUser, 'findOne', fakeInvitedUsers.findOne),
    ];

    try {
        const result = await backfillAccounts();

        assert.equal(result.usersLinked, 0);
        assert.equal(result.sharedAccountRowsSkipped, 1);
        assert.equal(fakeUsers.rows[0]!.account_id, null);
    } finally {
        afterLock = null;
        spies.forEach((spy) => spy.mock.restore());
    }
});

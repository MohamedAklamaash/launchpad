import { test, mock } from 'node:test';
import assert from 'node:assert/strict';

// env.ts validates process.env at import time via zod — dummy values only, never real
// secrets, and must be set before the first (dynamic) import of anything that pulls it in.
// Same preamble as src/service/invited-users/otp-attempt-cap.test.ts.
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

const { Account, InvitedUser, User } = await import('@/db');
const { backfillAccounts } = await import('@/service/account-backfill.service');
const { accountIdForEmail } = await import('@/utils/account-id');
const { Op } = await import('sequelize');

interface FakeRow {
    id: string;
    email: string;
    user_name: string;
    account_id: string | null;
    is_authenticated?: boolean;
}

// A tiny in-memory stand-in for the three tables the backfill touches, driving the real
// Sequelize model methods (mocked at the model boundary, never a live Postgres) the same
// way otp-attempt-cap.test.ts mocks UserOTP/RefreshToken.
const makeFakeTable = () => {
    const rows: FakeRow[] = [];
    return {
        rows,
        findAll: async () => rows.map((row) => ({ get: () => ({ ...row }) })),
        update: async (
            values: { account_id: string },
            opts: { where: { id: { [Op.in]: string[] }; account_id: null } },
        ) => {
            const ids = opts.where.id[Op.in];
            let affected = 0;
            for (const row of rows) {
                if (ids.includes(row.id) && row.account_id === null) {
                    row.account_id = values.account_id;
                    affected += 1;
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
        // Cross-table collision: same normalized email as user-1, deliberately not fixed
        // (F9 Non-goals) — both rows must end up sharing one account_id.
        {
            id: 'invited-1',
            email: '  owner@example.com  ',
            user_name: 'owner-invited',
            account_id: null,
        },
        { id: 'invited-2', email: 'third@example.com', user_name: 'third', account_id: null },
    );

    const findUsersSpy = mock.method(User, 'findAll', fakeUsers.findAll);
    const findInvitedSpy = mock.method(InvitedUser, 'findAll', fakeInvitedUsers.findAll);
    const findAccountsSpy = mock.method(Account, 'findAll', fakeAccounts.findAll);
    const bulkCreateSpy = mock.method(Account, 'bulkCreate', fakeAccounts.bulkCreate);
    const updateUsersSpy = mock.method(User, 'update', fakeUsers.update);
    const updateInvitedSpy = mock.method(InvitedUser, 'update', fakeInvitedUsers.update);

    try {
        const first = await backfillAccounts();

        assert.equal(first.accountsCreated, 3);
        assert.equal(first.usersLinked, 2);
        assert.equal(first.invitedUsersLinked, 2);
        assert.equal(first.collisionGroups, 1);

        const sharedId = accountIdForEmail('owner@example.com');
        assert.equal(fakeUsers.rows[0].account_id, sharedId);
        assert.equal(fakeInvitedUsers.rows[0].account_id, sharedId);
        assert.equal(fakeAccounts.rows.length, 3);

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

        assert.deepEqual(
            {
                users: fakeUsers.rows,
                invitedUsers: fakeInvitedUsers.rows,
                accounts: fakeAccounts.rows,
            },
            snapshotAfterFirst,
        );
    } finally {
        findUsersSpy.mock.restore();
        findInvitedSpy.mock.restore();
        findAccountsSpy.mock.restore();
        bulkCreateSpy.mock.restore();
        updateUsersSpy.mock.restore();
        updateInvitedSpy.mock.restore();
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
    }
});

test('backfillAccounts links a case-only collision within the same table to one account', async () => {
    // Postgres's unique index on `email` is case-sensitive, so `Foo@x.com` and `foo@x.com`
    // can coexist today within `users` itself (not just across tables) — the normalized-
    // email dedup must catch this one too.
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

    try {
        const result = await backfillAccounts();

        assert.equal(result.accountsCreated, 1);
        assert.equal(result.collisionGroups, 1);
        assert.equal(fakeUsers.rows[0].account_id, fakeUsers.rows[1].account_id);
    } finally {
        findUsersSpy.mock.restore();
        findInvitedSpy.mock.restore();
        findAccountsSpy.mock.restore();
        bulkCreateSpy.mock.restore();
        updateUsersSpy.mock.restore();
        updateInvitedSpy.mock.restore();
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
            is_authenticated: false,
        },
        {
            id: 'verified-invited-row',
            email: 'genuinely-verified@example.com',
            user_name: 'verified',
            account_id: null,
            is_authenticated: true,
        },
    );

    const findUsersSpy = mock.method(User, 'findAll', fakeUsers.findAll);
    const findInvitedSpy = mock.method(InvitedUser, 'findAll', fakeInvitedUsers.findAll);
    const findAccountsSpy = mock.method(Account, 'findAll', fakeAccounts.findAll);
    const bulkCreateSpy = mock.method(Account, 'bulkCreate', fakeAccounts.bulkCreate);
    const updateUsersSpy = mock.method(User, 'update', fakeUsers.update);
    const updateInvitedSpy = mock.method(InvitedUser, 'update', fakeInvitedUsers.update);

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
    }
});

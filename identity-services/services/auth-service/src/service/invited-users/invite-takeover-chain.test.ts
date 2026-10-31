import { test, mock, beforeEach, after } from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';

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
process.env.ORGS_ENABLED = 'true';

// The whole attack, driven through the real services against an in-memory stand-in for
// users / invited_users / user_otps (mocked at the model + raw-query boundary):
// an inviter registers a victim's email with a password of their choosing, the victim
// verifies the code, the account backfill runs, and the victim's personal org is ensured.
const Redis = (await import('ioredis')).default;
const { Op } = await import('sequelize');
const { HttpError } = await import('@launchpad/common');
const { sequelize } = await import('@/db/sequalize');
const { Account, InvitedUser, RefreshToken, User, UserOTP } = await import('@/db');
const messageModule = await import('@/messaging/producer/user-created.message');
const { InvitedUserService } = await import('@/service/invited-users/inviter-user.crud.service');
const { InvitedUserAuthService } =
    await import('@/service/invited-users/invited-user.auth.service');
const { backfillAccounts } = await import('@/service/account-backfill.service');
const { ensurePersonalOrganization } = await import('@/service/organizations/personal-org.service');
const { accountIdForEmail } = await import('@/utils/account-id');
const { personalOrgId } = await import('@/utils/org-id');
const { installPersonalOrgStore } = await import('@/test-support/personal-org-store');

type Row = Record<string, unknown>;

const state = {
    users: [] as Row[],
    invited: [] as Row[],
    otps: [] as Row[],
};

const matches = (row: Row, where: Row = {}): boolean =>
    Object.entries(where).every(([key, value]) => {
        if (value !== null && typeof value === 'object') {
            const ne = (value as { [Op.ne]?: unknown })[Op.ne];
            return ne === undefined ? true : row[key] !== ne;
        }
        return row[key] === value;
    });

const asModelRow = (row: Row) => ({ ...row, get: () => ({ ...row }) });

const VICTIM_EMAIL = 'victim@example.com';
const ATTACKER_PASSWORD = 'attacker-chosen-pw';
const VICTIM_PASSWORD = 'victim-own-pw1';

const victimUser = () => ({
    id: randomUUID(),
    email: VICTIM_EMAIL,
    user_name: 'victim',
    account_id: accountIdForEmail(VICTIM_EMAIL),
});

const orgStore = await installPersonalOrgStore();

mock.method(sequelize, 'transaction', async (cb: (t: object) => unknown) =>
    cb({ LOCK: { UPDATE: 'UPDATE' } }),
);
mock.method(Redis.prototype, 'eval', async () => 1);
mock.method(messageModule.userAuthenticationQueue, 'add', async () => undefined as never);

mock.method(sequelize, 'query', async (sql: string, opts: { replacements?: Row }) => {
    const email = opts.replacements?.email;
    if (sql.includes('failed_otp_attempts = CASE')) {
        const row = state.invited.find((r) => r.email === email);
        return row ? [{ ...row, failed_otp_attempts: 1 }] : [];
    }
    if (sql.includes('SET is_authenticated = true')) {
        assert.match(
            sql,
            /password_hash = CASE\s+WHEN email_verified_at IS NULL THEN :passwordHash/,
        );
        const row = state.invited.find((r) => r.id === opts.replacements?.userId)!;
        const firstVerification = row.email_verified_at === null;
        row.is_authenticated = true;
        row.email_verified_at ??= new Date();
        if (firstVerification) row.password_hash = opts.replacements?.passwordHash;
        return [{ email_verified_at: row.email_verified_at }];
    }
    return [];
});

mock.method(User, 'findAll', async () => state.users.map(asModelRow));
mock.method(User, 'findOne', async (o: { where: Row }) => {
    const row = state.users.find((r) => matches(r, o.where));
    return row ? asModelRow(row) : null;
});
mock.method(User, 'update', async (values: Row, o: { where: Row }) => {
    const hits = state.users.filter((r) => matches(r, o.where));
    hits.forEach((r) => Object.assign(r, values));
    return [hits.length];
});

mock.method(InvitedUser, 'findAll', async () => state.invited.map(asModelRow));
mock.method(InvitedUser, 'findOne', async (o: { where: Row }) => {
    const row = state.invited.find((r) => matches(r, o.where));
    return row ?? null;
});
mock.method(InvitedUser, 'create', async (attrs: Row) => {
    const row: Row = {
        id: randomUUID(),
        account_id: null,
        email_verified_at: null,
        created_at: new Date(),
        updated_at: new Date(),
        async save() {
            return this;
        },
        ...attrs,
    };
    state.invited.push(row);
    return row;
});
mock.method(InvitedUser, 'update', async (values: Row, o: { where: Row }) => {
    const ids = (o.where.id as { [Op.in]?: string[] } | undefined)?.[Op.in];
    const hits = state.invited.filter((r) =>
        ids
            ? ids.includes(r.id as string) && r.account_id === null
            : r.email_verified_at === null && r.account_id !== null,
    );
    hits.forEach((r) => Object.assign(r, values));
    return [hits.length];
});

mock.method(Account, 'findAll', async () => []);
mock.method(Account, 'bulkCreate', async () => []);
mock.method(Account, 'findOrCreate', async () => [{}, true]);

mock.method(UserOTP, 'destroy', async () => 0);
mock.method(UserOTP, 'create', async (attrs: Row) => {
    const row = { ...attrs, otp: '123456', destroy: async () => undefined };
    state.otps.push(row);
    return row;
});
mock.method(
    UserOTP,
    'findOne',
    async (o: { where: Row }) =>
        state.otps.find((r) => r.invited_user_id === o.where.invited_user_id) ?? null,
);
mock.method(RefreshToken, 'create', async (attrs: object) => ({ ...attrs, token_id: 'tok-1' }));

const inviter = new InvitedUserService();
const auth = new InvitedUserAuthService();

const inviteVictim = async () => {
    const originalConsoleError = console.error;
    console.error = () => {};
    try {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const role = 'user' as any;
        return await inviter.register(
            {
                email: VICTIM_EMAIL,
                password: ATTACKER_PASSWORD,
                user_name: 'invitee',
                infra_id: randomUUID(),
                role,
            },
            'attacker-user-id',
        );
    } finally {
        console.error = originalConsoleError;
    }
};

const loginFails = (password: string) =>
    assert.rejects(
        () => auth.login({ email: VICTIM_EMAIL, password }),
        (error: unknown) => error instanceof HttpError && error.statusCode === 401,
    );

beforeEach(() => {
    state.users.length = 0;
    state.invited.length = 0;
    state.otps.length = 0;
    orgStore.orgs.clear();
    orgStore.memberships.length = 0;
    orgStore.published.length = 0;
});

after(() => mock.restoreAll());

test('exploit chain: invite victim email, victim verifies, backfill, ensure - the invited row never reaches the victim account or org', async () => {
    const victim = victimUser();
    state.users.push(victim);

    const { otp } = await inviteVictim();
    await auth.authenticateWithOTP({ email: VICTIM_EMAIL, otp: otp! });

    await loginFails(ATTACKER_PASSWORD);

    const backfill = await backfillAccounts();
    const invitedRow = state.invited[0]!;
    assert.equal(invitedRow.account_id, null);
    assert.equal(backfill.invitedUsersLinked, 0);
    assert.equal(backfill.sharedAccountRowsSkipped, 1);

    const status = await ensurePersonalOrganization({
        id: victim.id,
        account_id: victim.account_id,
        user_name: victim.user_name,
    });
    assert.equal(status, 'created');
    assert.equal(orgStore.orgs.get(personalOrgId(victim.id))?.owner_account_id, victim.account_id);
    assert.equal(
        state.invited.some((r) => r.account_id === victim.account_id),
        false,
    );
});

test('legacy damage: an invited row already sharing the victim account blocks the personal org', async () => {
    const victim = victimUser();
    state.users.push(victim);
    state.invited.push({
        id: randomUUID(),
        email: VICTIM_EMAIL,
        account_id: victim.account_id,
        email_verified_at: new Date(),
    });

    const status = await ensurePersonalOrganization({
        id: victim.id,
        account_id: victim.account_id,
        user_name: victim.user_name,
    });

    assert.equal(status, 'shared_account');
    assert.equal(orgStore.orgs.size, 0);
    assert.equal(orgStore.published.length, 0);
});

test('legacy damage: another User row sharing the account blocks the personal org', async () => {
    const victim = victimUser();
    state.users.push(victim, { ...victimUser(), user_name: 'twin' });

    const status = await ensurePersonalOrganization({
        id: victim.id,
        account_id: victim.account_id,
        user_name: victim.user_name,
    });

    assert.equal(status, 'shared_account');
    assert.equal(orgStore.orgs.size, 0);
});

test('first verification discards the inviter-chosen password and, without new_password, leaves no usable one', async () => {
    const { otp } = await inviteVictim();
    await loginFails(ATTACKER_PASSWORD);

    await auth.authenticateWithOTP({ email: VICTIM_EMAIL, otp: otp! });

    await loginFails(ATTACKER_PASSWORD);
    await loginFails('');
});

test('first verification with new_password lets the invitee log in with it, and not with the inviter password', async () => {
    const { otp } = await inviteVictim();

    await auth.authenticateWithOTP({
        email: VICTIM_EMAIL,
        otp: otp!,
        newPassword: VICTIM_PASSWORD,
    });

    const session = await auth.login({ email: VICTIM_EMAIL, password: VICTIM_PASSWORD });
    assert.ok(session.accessToken);
    await loginFails(ATTACKER_PASSWORD);
});

test('re-verifying an already verified user (invited to another infra) keeps their password', async () => {
    const first = await inviteVictim();
    await auth.authenticateWithOTP({
        email: VICTIM_EMAIL,
        otp: first.otp!,
        newPassword: VICTIM_PASSWORD,
    });

    const second = await inviteVictim();
    assert.ok(second.otp);
    await auth.authenticateWithOTP({ email: VICTIM_EMAIL, otp: second.otp });

    const session = await auth.login({ email: VICTIM_EMAIL, password: VICTIM_PASSWORD });
    assert.ok(session.accessToken);
});

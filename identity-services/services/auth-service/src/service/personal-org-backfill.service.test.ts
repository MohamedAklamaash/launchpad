import { test, mock, beforeEach } from 'node:test';
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
process.env.ORGS_ENABLED = 'true';

const { Op } = await import('sequelize');
const { sequelize } = await import('@/db/sequalize');
const { InvitedUser, User } = await import('@/db');
const { backfillPersonalOrgs } = await import('@/service/personal-org-backfill.service');
const { personalOrgId } = await import('@/utils/org-id');
const { installPersonalOrgStore } = await import('@/test-support/personal-org-store');

mock.method(sequelize, 'transaction', async (cb: (t: object) => unknown) => cb({}));
const store = await installPersonalOrgStore();

const OWNER_ID = '11111111-1111-4111-8111-111111111111';
const OWNER_ACCOUNT = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const OTHER_ID = '22222222-2222-4222-8222-222222222222';
const OTHER_ACCOUNT = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
const UNLINKED_ID = '33333333-3333-4333-8333-333333333333';

let userRows: { id: string; account_id: string | null; user_name: string }[] = [];
mock.method(User, 'findAll', async () => userRows);
let invitedHolders: { id: string; account_id: string }[] = [];
mock.method(
    User,
    'findOne',
    async (o: { where: { account_id: string; id: { [Op.ne]: string } } }) =>
        userRows.find((u) => u.account_id === o.where.account_id && u.id !== o.where.id[Op.ne]) ??
        null,
);
mock.method(
    InvitedUser,
    'findOne',
    async (o: { where: { account_id: string } }) =>
        invitedHolders.find((r) => r.account_id === o.where.account_id) ?? null,
);
const invitedFindAll = mock.method(InvitedUser, 'findAll', async () => {
    throw new Error('the personal-org backfill must never read invited_users');
});

beforeEach(() => {
    store.orgs.clear();
    store.memberships.length = 0;
    store.published.length = 0;
    invitedHolders = [];
    userRows = [
        { id: OWNER_ID, account_id: OWNER_ACCOUNT, user_name: 'octocat' },
        { id: OTHER_ID, account_id: OTHER_ACCOUNT, user_name: 'hubot' },
        { id: UNLINKED_ID, account_id: null, user_name: 'held-for-review' },
    ];
});

test('creates a deterministic personal org plus owner membership per linked user and publishes org.created', async () => {
    const result = await backfillPersonalOrgs();

    assert.deepEqual(result, {
        created: 2,
        alreadyExisted: 0,
        skippedUnlinked: 1,
        conflicts: 0,
        sharedAccounts: 0,
        inconsistent: 0,
        failed: 0,
    });

    const org = store.orgs.get(personalOrgId(OWNER_ID));
    assert.equal(org?.owner_account_id, OWNER_ACCOUNT);
    assert.equal(org?.name, 'octocat');
    const owners = store.memberships.filter(
        (m) => m.organization_id === personalOrgId(OWNER_ID) && m.role === 'owner',
    );
    assert.deepEqual(
        owners.map((m) => m.account_id),
        [OWNER_ACCOUNT],
    );

    assert.deepEqual(store.published.map((e) => e.routingKey).sort(), [
        'org.created',
        'org.created',
    ]);
    assert.deepEqual(
        store.published.map((e) => e.payload.organization_id).sort(),
        [personalOrgId(OWNER_ID), personalOrgId(OTHER_ID)].sort(),
    );
    assert.equal(invitedFindAll.mock.callCount(), 0);
});

test('a second run is a no-op: nothing created, nothing republished', async () => {
    await backfillPersonalOrgs();
    store.published.length = 0;

    const result = await backfillPersonalOrgs();

    assert.equal(result.created, 0);
    assert.equal(result.alreadyExisted, 2);
    assert.equal(store.orgs.size, 2);
    assert.equal(store.memberships.length, 2);
    assert.equal(store.published.length, 0);
});

test('never steals an org that exists with a different owner: left untouched and reported', async () => {
    const victimOrg = personalOrgId(OWNER_ID);
    store.orgs.set(victimOrg, {
        id: victimOrg,
        owner_account_id: OTHER_ACCOUNT,
        name: 'pre-existing',
    });
    store.memberships.push({
        id: 'm-existing',
        organization_id: victimOrg,
        account_id: OTHER_ACCOUNT,
        role: 'owner',
        version: 4,
        removed_at: null,
    });

    const result = await backfillPersonalOrgs();

    assert.equal(result.conflicts, 1);
    assert.equal(result.created, 1);
    assert.equal(store.orgs.get(victimOrg)?.owner_account_id, OTHER_ACCOUNT);
    assert.equal(store.orgs.get(victimOrg)?.name, 'pre-existing');
    assert.equal(store.memberships.filter((m) => m.organization_id === victimOrg).length, 1);
    assert.equal(
        store.published.some((e) => e.payload.organization_id === victimOrg),
        false,
    );
});

test('users without a linked account get no org and no event', async () => {
    const result = await backfillPersonalOrgs();

    assert.equal(result.skippedUnlinked, 1);
    assert.equal(store.orgs.has(personalOrgId(UNLINKED_ID)), false);
    assert.equal(
        store.published.some((e) => e.payload.organization_id === personalOrgId(UNLINKED_ID)),
        false,
    );
});

test('an existing org whose owner membership is missing is reported, not repaired', async () => {
    const orgId = personalOrgId(OWNER_ID);
    store.orgs.set(orgId, { id: orgId, owner_account_id: OWNER_ACCOUNT, name: 'octocat' });

    const result = await backfillPersonalOrgs();

    assert.equal(result.inconsistent, 1);
    assert.equal(store.memberships.filter((m) => m.organization_id === orgId).length, 0);
});

test('republishExisting re-emits org.created for a correctly owned org without writing rows', async () => {
    await backfillPersonalOrgs();
    store.published.length = 0;

    const result = await backfillPersonalOrgs({ republishExisting: true });

    assert.equal(result.alreadyExisted, 2);
    assert.equal(store.orgs.size, 2);
    assert.equal(store.memberships.length, 2);
    assert.deepEqual(
        store.published.map((e) => e.routingKey),
        ['org.created', 'org.created'],
    );
    assert.ok(store.published.every((e) => e.payload.version === 1));
});

test('republishExisting publishes the owner membership at its current version, not the creation version', async () => {
    await backfillPersonalOrgs();
    const orgId = personalOrgId(OWNER_ID);
    const ownerRow = store.memberships.find((m) => m.organization_id === orgId)!;
    ownerRow.version = 7;
    store.published.length = 0;

    await backfillPersonalOrgs({ republishExisting: true });

    const event = store.published.find((e) => e.payload.organization_id === orgId)!;
    assert.equal(event.payload.version, 7);
    assert.equal(event.payload.owner_account_id, OWNER_ACCOUNT);
});

test('republishExisting after ownership moved away publishes nothing for that org and never resurrects the old owner', async () => {
    await backfillPersonalOrgs();
    const orgId = personalOrgId(OWNER_ID);
    store.orgs.get(orgId)!.owner_account_id = OTHER_ACCOUNT;
    const oldOwnerRow = store.memberships.find((m) => m.organization_id === orgId)!;
    oldOwnerRow.role = 'admin';
    oldOwnerRow.version = 2;
    store.memberships.push({
        id: 'm-new-owner',
        organization_id: orgId,
        account_id: OTHER_ACCOUNT,
        role: 'owner',
        version: 2,
        removed_at: null,
    });
    store.published.length = 0;

    const result = await backfillPersonalOrgs({ republishExisting: true });

    assert.equal(result.conflicts, 1);
    assert.equal(
        store.published.some(
            (e) =>
                e.payload.organization_id === orgId && e.payload.owner_account_id === OWNER_ACCOUNT,
        ),
        false,
    );
});

test('one user failing does not abort the pass', async () => {
    const originalFindByPk = (await import('@/db')).Organization.findByPk;
    const failing = mock.method(
        (await import('@/db')).Organization,
        'findByPk',
        async (id: string) => {
            if (id === personalOrgId(OWNER_ID)) throw new Error('db hiccup');
            return originalFindByPk.call(null as never, id);
        },
    );
    try {
        const result = await backfillPersonalOrgs();
        assert.equal(result.failed, 1);
        assert.equal(store.orgs.has(personalOrgId(OTHER_ID)), true);
    } finally {
        failing.mock.restore();
    }
});

test('a user whose account is also held by an invited row gets no org and no event', async () => {
    invitedHolders = [{ id: 'invited-row', account_id: OWNER_ACCOUNT }];

    const result = await backfillPersonalOrgs();

    assert.equal(result.sharedAccounts, 1);
    assert.equal(result.created, 1);
    assert.equal(store.orgs.has(personalOrgId(OWNER_ID)), false);
    assert.equal(
        store.published.some((e) => e.payload.organization_id === personalOrgId(OWNER_ID)),
        false,
    );
});

test('two users sharing one account both get no org', async () => {
    userRows = [
        { id: OWNER_ID, account_id: OWNER_ACCOUNT, user_name: 'octocat' },
        { id: OTHER_ID, account_id: OWNER_ACCOUNT, user_name: 'hubot' },
    ];

    const result = await backfillPersonalOrgs();

    assert.equal(result.sharedAccounts, 2);
    assert.equal(store.orgs.size, 0);
});

test('an existing org owned by a shared account is reported, not republished', async () => {
    const orgId = personalOrgId(OWNER_ID);
    store.orgs.set(orgId, { id: orgId, owner_account_id: OWNER_ACCOUNT, name: 'octocat' });
    invitedHolders = [{ id: 'invited-row', account_id: OWNER_ACCOUNT }];

    const result = await backfillPersonalOrgs({ republishExisting: true });

    assert.equal(result.sharedAccounts, 1);
    assert.equal(
        store.published.some((e) => e.payload.organization_id === orgId),
        false,
    );
});

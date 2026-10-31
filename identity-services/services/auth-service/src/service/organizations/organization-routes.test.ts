import { test } from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';

const { createHarness } = await import('@/test-support/org-invitation-harness');
const { personalOrgId } = await import('@/utils/org-id');
const { MAX_ORG_CREATES_PER_WINDOW } = await import('@/utils/otp-attempts');

type Harness = Awaited<ReturnType<typeof createHarness>>;

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

const createOrg = (h: Harness, bearer: string, body: unknown) =>
    h.call('POST', '/organizations', { bearer, body });

test('GET /organizations lists only the caller ACTIVE memberships, with role and is_personal', async () => {
    const h = await createHarness();
    try {
        const me = h.addCredential('users');
        const personal = personalOrgId(me.id);
        h.addOrg('My Home', me.account_id as string, personal);
        h.addMembership(personal, me.account_id as string, 'owner');

        const other = h.addCredential('users');
        const team = h.addOrg('Team', other.account_id as string);
        h.addMembership(team, other.account_id as string, 'owner');
        h.addMembership(team, me.account_id as string, 'viewer');

        const left = h.addOrg('Left', other.account_id as string);
        h.addMembership(left, other.account_id as string, 'owner');
        h.addMembership(left, me.account_id as string, 'admin', new Date());

        const stranger = h.addOrg('Stranger', other.account_id as string);
        h.addMembership(stranger, other.account_id as string, 'owner');

        const res = await h.call('GET', '/organizations', { bearer: h.tokenFor(me) });

        assert.equal(res.status, 200);
        assert.deepEqual(res.body, [
            { id: personal, name: 'My Home', role: 'owner', is_personal: true },
            { id: team, name: 'Team', role: 'viewer', is_personal: false },
        ]);
    } finally {
        await h.close();
    }
});

test('a personal org the caller was invited into is still reported as personal', async () => {
    const h = await createHarness();
    try {
        const owner = h.addCredential('users');
        const home = personalOrgId(owner.id);
        h.addOrg('Owner Home', owner.account_id as string, home);
        h.addMembership(home, owner.account_id as string, 'owner');
        const guest = h.addCredential('invitedUsers');
        h.addMembership(home, guest.account_id as string, 'developer');

        const res = await h.call('GET', '/organizations', { bearer: h.tokenFor(guest) });

        assert.deepEqual(res.body, [
            { id: home, name: 'Owner Home', role: 'developer', is_personal: true },
        ]);
    } finally {
        await h.close();
    }
});

test('listing needs a verified credential linked to the token account: unverified, mismatched and claim-less callers get 403', async () => {
    const h = await createHarness();
    try {
        const unverified = h.addCredential('invitedUsers', { email_verified_at: null });
        const linked = h.addCredential('users');
        const mismatched = h.addCredential('users');
        const claimless = h.addCredential('users', { account_id: null });

        const calls = [
            h.tokenFor(unverified),
            h.tokenFor(mismatched, { account_id: linked.account_id }),
            h.tokenFor(claimless),
        ];
        for (const bearer of calls) {
            assert.equal((await h.call('GET', '/organizations', { bearer })).status, 403);
        }
        assert.equal((await h.call('GET', '/organizations')).status, 401);
    } finally {
        await h.close();
    }
});

test('POST /organizations: the caller becomes owner of a server-generated team org, the event goes through the outbox, and the name is sanitized', async () => {
    const h = await createHarness();
    try {
        const me = h.addCredential('users');
        const res = await createOrg(h, h.tokenFor(me), {
            name: '  Acme Inc <b>https://evil.example</b>  ',
        });

        assert.equal(res.status, 201);
        const id = res.body?.id as string;
        assert.match(id, UUID);
        assert.notEqual(id, personalOrgId(me.id));
        assert.deepEqual(res.body, {
            id,
            name: 'Acme Inc b https evil example b',
            role: 'owner',
            is_personal: false,
        });
        assert.equal(h.state.orgCreateAttrs[0]!.id, undefined);
        assert.equal(h.state.orgs[0]!.name, 'Acme Inc b https evil example b');

        const owners = h.state.memberships.filter((m) => m.organization_id === id);
        assert.deepEqual(
            owners.map((m) => [m.account_id, m.role]),
            [[me.account_id, 'owner']],
        );
        assert.equal(h.state.outbox.length, 1);
        assert.equal(h.state.outbox[0]!.routing_key, 'org.created');
        assert.deepEqual(
            h.state.published.map((p) => [p.routingKey, p.payload.organization_id]),
            [['org.created', id]],
        );
    } finally {
        await h.close();
    }
});

test('the org name is clipped to 64 characters after NFKC normalisation', async () => {
    const h = await createHarness();
    try {
        const me = h.addCredential('users');
        const res = await createOrg(h, h.tokenFor(me), { name: `Ａ${'b'.repeat(190)}` });

        assert.equal(res.status, 201);
        assert.equal(res.body?.name, `A${'b'.repeat(63)}`);
    } finally {
        await h.close();
    }
});

test('every create asks the model for a fresh id, so two creates differ and neither is the personal-org id', async () => {
    const h = await createHarness();
    try {
        const me = h.addCredential('users');
        const bearer = h.tokenFor(me);
        const a = await createOrg(h, bearer, { name: 'One' });
        const b = await createOrg(h, bearer, { name: 'Two' });

        assert.notEqual(a.body?.id, b.body?.id);
        assert.notEqual(a.body?.id, personalOrgId(me.id));
        assert.notEqual(b.body?.id, personalOrgId(me.id));
        assert.deepEqual(
            h.state.orgCreateAttrs.map((attrs) => attrs.id),
            [undefined, undefined],
        );
    } finally {
        await h.close();
    }
});

test('a client-chosen id is a 422 and nothing is created, so a personal-org id cannot be squatted', async () => {
    const h = await createHarness();
    try {
        const me = h.addCredential('users');
        const victim = randomUUID();
        for (const body of [
            { name: 'Squat', id: personalOrgId(victim) },
            { name: 'Squat', organization_id: personalOrgId(victim) },
            { name: 'Squat', owner_account_id: randomUUID() },
        ]) {
            const res = await createOrg(h, h.tokenFor(me), body);
            assert.equal(res.status, 422);
        }
        assert.equal(h.state.orgs.length, 0);
        assert.equal(h.state.memberships.length, 0);
        assert.equal(h.state.outbox.length, 0);
        assert.equal(h.state.redisCounts.size, 0);
    } finally {
        await h.close();
    }
});

test('invalid names are rejected before the limiter is spent', async () => {
    const h = await createHarness();
    try {
        const me = h.addCredential('users');
        for (const body of [
            {},
            { name: '' },
            { name: 7 },
            { name: 'x'.repeat(201) },
            { name: '@@//::..' },
        ]) {
            const res = await createOrg(h, h.tokenFor(me), body);
            assert.equal(res.status, 422, JSON.stringify(body));
        }
        assert.equal(h.state.orgs.length, 0);
        assert.equal(h.state.redisCounts.size, 0);
    } finally {
        await h.close();
    }
});

test('creating needs a verified credential whose Account is not shared; nothing is spent or written otherwise', async () => {
    const h = await createHarness();
    try {
        const unverified = h.addCredential('invitedUsers', { email_verified_at: null });
        const sharedUser = h.addCredential('users');
        h.addCredential('invitedUsers', {
            email: 'twin@example.com',
            account_id: sharedUser.account_id,
        });
        const claimless = h.addCredential('users', { account_id: null });

        for (const bearer of [
            h.tokenFor(unverified),
            h.tokenFor(sharedUser),
            h.tokenFor(claimless),
        ]) {
            assert.equal((await createOrg(h, bearer, { name: 'Nope' })).status, 403);
        }
        assert.equal(h.state.orgs.length, 0);
        assert.equal(h.state.redisCounts.size, 0);
    } finally {
        await h.close();
    }
});

test('a verified password account (not only GitHub) can create a team org', async () => {
    const h = await createHarness();
    try {
        const me = h.addCredential('invitedUsers');
        assert.equal((await createOrg(h, h.tokenFor(me), { name: 'Pw Team' })).status, 201);
    } finally {
        await h.close();
    }
});

test('creation is limited per account, and a different account is unaffected', async () => {
    const h = await createHarness();
    try {
        const me = h.addCredential('users');
        const bearer = h.tokenFor(me);
        for (let i = 0; i < MAX_ORG_CREATES_PER_WINDOW; i++) {
            assert.equal((await createOrg(h, bearer, { name: `Org ${i}` })).status, 201);
        }
        const blocked = await createOrg(h, bearer, { name: 'Too many' });

        assert.equal(blocked.status, 429);
        assert.equal(h.state.orgs.length, MAX_ORG_CREATES_PER_WINDOW);
        const other = h.addCredential('users');
        assert.equal((await createOrg(h, h.tokenFor(other), { name: 'Fine' })).status, 201);
    } finally {
        await h.close();
    }
});

test('creation fails closed with 503 when the limiter is down', async () => {
    const h = await createHarness();
    try {
        const me = h.addCredential('users');
        h.state.redisDown = true;
        const res = await createOrg(h, h.tokenFor(me), { name: 'Down' });

        assert.equal(res.status, 503);
        assert.equal(h.state.orgs.length, 0);
        assert.equal(h.state.outbox.length, 0);
    } finally {
        await h.close();
    }
});

test('a failed outbox write rolls the new org back entirely', async () => {
    const h = await createHarness();
    try {
        const me = h.addCredential('users');
        h.state.outboxFailOnCall = 1;
        const res = await createOrg(h, h.tokenFor(me), { name: 'Rollback' });

        assert.equal(res.status, 500);
        assert.equal(h.state.orgs.length, 0);
        assert.equal(h.state.memberships.length, 0);
        assert.equal(h.state.published.length, 0);
    } finally {
        await h.close();
    }
});

test('rename: owner and admin can, developer and viewer get 403, a non-member gets the missing-org 404', async () => {
    for (const [role, expected] of [
        ['owner', 200],
        ['admin', 200],
        ['developer', 403],
        ['viewer', 403],
    ] as const) {
        const h = await createHarness();
        try {
            const owner = h.addCredential('users');
            const orgId = h.addOrg('Old', owner.account_id as string);
            h.addMembership(orgId, owner.account_id as string, 'owner');
            const caller = role === 'owner' ? owner : h.addCredential('invitedUsers');
            if (role !== 'owner') h.addMembership(orgId, caller.account_id as string, role);

            const res = await h.call('PATCH', `/organizations/${orgId}`, {
                bearer: h.tokenFor(caller),
                body: { name: 'New <script>' },
            });

            assert.equal(res.status, expected, role);
            assert.equal(h.state.orgs[0]!.name, expected === 200 ? 'New script' : 'Old');
            assert.equal(
                h.state.audit.filter((a) => a.action === 'organization.renamed').length,
                expected === 200 ? 1 : 0,
            );
        } finally {
            await h.close();
        }
    }

    const h = await createHarness();
    try {
        const owner = h.addCredential('users');
        const orgId = h.addOrg('Old', owner.account_id as string);
        h.addMembership(orgId, owner.account_id as string, 'owner');
        const outsider = h.addCredential('users');
        const body = { name: 'Hijack' };
        const real = await h.call('PATCH', `/organizations/${orgId}`, {
            bearer: h.tokenFor(outsider),
            body,
        });
        const missing = await h.call('PATCH', `/organizations/${randomUUID()}`, {
            bearer: h.tokenFor(outsider),
            body,
        });

        assert.equal(real.status, 404);
        assert.deepEqual(real.body, missing.body);
        assert.equal(h.state.orgs[0]!.name, 'Old');
    } finally {
        await h.close();
    }
});

test('a personal org can be renamed by its owner, and an admin of org A cannot rename org B', async () => {
    const h = await createHarness();
    try {
        const owner = h.addCredential('users');
        const home = personalOrgId(owner.id);
        h.addOrg('Home', owner.account_id as string, home);
        h.addMembership(home, owner.account_id as string, 'owner');
        const renamed = await h.call('PATCH', `/organizations/${home}`, {
            bearer: h.tokenFor(owner),
            body: { name: 'Renamed Home' },
        });
        assert.equal(renamed.status, 200);

        const adminA = h.addCredential('users');
        const orgA = h.addOrg('A', adminA.account_id as string);
        h.addMembership(orgA, adminA.account_id as string, 'admin');
        const cross = await h.call('PATCH', `/organizations/${home}`, {
            bearer: h.tokenFor(adminA),
            body: { name: 'Pwned' },
        });
        assert.equal(cross.status, 404);
        assert.equal(h.state.orgs.find((o) => o.id === home)!.name, 'Renamed Home');
    } finally {
        await h.close();
    }
});

test('there is no way to delete an organization', async () => {
    const h = await createHarness();
    try {
        const owner = h.addCredential('users');
        const orgId = h.addOrg('Keep', owner.account_id as string);
        h.addMembership(orgId, owner.account_id as string, 'owner');

        const res = await h.call('DELETE', `/organizations/${orgId}`, {
            bearer: h.tokenFor(owner),
        });

        assert.equal(res.status, 404);
        assert.equal(h.state.orgs.length, 1);
    } finally {
        await h.close();
    }
});

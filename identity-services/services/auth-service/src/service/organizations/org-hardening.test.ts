import { test } from 'node:test';
import assert from 'node:assert/strict';

const { createHarness } = await import('@/test-support/org-invitation-harness');
const { changeMemberRole, removeMember } = await import('./membership.service');
const { MAX_OWNED_ORGS } = await import('./organization-admin.service');
const { MAX_ORG_ACTIONS_PER_WINDOW } = await import('@/utils/otp-attempts');
const { registerSchema } = await import('@/schemas/invited-user.schema');

type Harness = Awaited<ReturnType<typeof createHarness>>;

const org = async () => {
    const h = await createHarness();
    const owner = h.addCredential('users');
    h.addAccount(owner);
    const orgId = h.addOrg('Acme', owner.account_id as string);
    h.addMembership(orgId, owner.account_id as string, 'owner');
    const admin = h.addCredential('invitedUsers');
    h.addAccount(admin);
    const adminRow = h.addMembership(orgId, admin.account_id as string, 'admin');
    const dev = h.addCredential('invitedUsers');
    h.addAccount(dev);
    const devRow = h.addMembership(orgId, dev.account_id as string, 'developer');
    return { h, owner, orgId, admin, adminRow, dev, devRow };
};

const names = async (h: Harness, orgId: string, who: Parameters<Harness['tokenFor']>[0]) =>
    (
        (await h.call('GET', `/organizations/${orgId}/members`, { bearer: h.tokenFor(who) }))
            .body as unknown as { display_name: string }[]
    ).map((m) => m.display_name);

test('member names are sanitized for every caller, and a viewer gets Member for anything with an @', async () => {
    const { h, owner, orgId, admin, dev } = await org();
    try {
        const viewer = h.addCredential('invitedUsers');
        h.addAccount(viewer);
        h.addMembership(orgId, viewer.account_id as string, 'viewer');
        h.state.accounts.find((a) => a.id === owner.account_id)!.display_name =
            'alice@corp.example';
        h.state.accounts.find((a) => a.id === admin.account_id)!.display_name =
            '<b>Bob</b> https://evil.example';
        h.state.accounts.find((a) => a.id === dev.account_id)!.display_name = 'x'.repeat(500);

        const forAdmin = await names(h, orgId, admin);
        const forViewer = await names(h, orgId, viewer);

        assert.deepEqual(forAdmin, [
            'alice corp example',
            'b Bob b https evil example',
            'x'.repeat(64),
            'tester',
        ]);
        assert.deepEqual(forViewer, [
            'Member',
            'b Bob b https evil example',
            'x'.repeat(64),
            'tester',
        ]);
    } finally {
        await h.close();
    }
});

test('team org creation is capped per account at 20 owned orgs, with a 409', async () => {
    const h = await createHarness();
    try {
        const me = h.addCredential('users');
        for (let i = 0; i < MAX_OWNED_ORGS; i++) {
            const id = h.addOrg(`o${i}`, me.account_id as string);
            h.addMembership(id, me.account_id as string, 'owner');
        }
        const res = await h.call('POST', '/organizations', {
            bearer: h.tokenFor(me),
            body: { name: 'One too many' },
        });

        assert.equal(res.status, 409);
        assert.match(String(res.body?.message), /at most 20/);
        assert.equal(h.state.orgs.length, MAX_OWNED_ORGS);
    } finally {
        await h.close();
    }
});

test('the creating account is recorded as actor on the organization.created audit row', async () => {
    const h = await createHarness();
    try {
        const me = h.addCredential('users');
        await h.call('POST', '/organizations', { bearer: h.tokenFor(me), body: { name: 'Team' } });

        const row = h.state.audit.find((a) => a.action === 'organization.created')!;
        assert.equal(row.actor_account_id, me.account_id);
        assert.equal(row.target_account_id, me.account_id);
    } finally {
        await h.close();
    }
});

test('rename, role change and remove share a per-(org, actor) limit and fail closed', async () => {
    const { h, admin, orgId, dev, devRow } = await org();
    try {
        const bearer = h.tokenFor(admin);
        const key = `budget:org-action:${orgId}:${admin.account_id}`;
        h.state.redisCounts.set(key, MAX_ORG_ACTIONS_PER_WINDOW);

        const calls = [
            h.call('PATCH', `/organizations/${orgId}`, { bearer, body: { name: 'New' } }),
            h.call('PATCH', `/organizations/${orgId}/members/${dev.account_id}`, {
                bearer,
                body: { role: 'viewer' },
            }),
            h.call('DELETE', `/organizations/${orgId}/members/${dev.account_id}`, { bearer }),
        ];
        for (const res of await Promise.all(calls)) assert.equal(res.status, 429);
        assert.equal(devRow.role, 'developer');
        assert.equal(devRow.removed_at, null);
        assert.equal(h.state.orgs[0]!.name, 'Acme');

        h.state.redisCounts.clear();
        h.state.redisDown = true;
        const down = await h.call('DELETE', `/organizations/${orgId}/members/${dev.account_id}`, {
            bearer,
        });
        assert.equal(down.status, 503);
        assert.equal(devRow.removed_at, null);
    } finally {
        await h.close();
    }
});

test('leaving is not counted against the action limit', async () => {
    const { h, dev, orgId } = await org();
    try {
        const res = await h.call('DELETE', `/organizations/${orgId}/members/${dev.account_id}`, {
            bearer: h.tokenFor(dev),
        });
        assert.equal(res.status, 204);
        assert.equal(h.state.redisCounts.size, 0);
    } finally {
        await h.close();
    }
});

test('under the transaction lock, an actor demoted or removed after the first check cannot land a role change or removal', async () => {
    const { h, orgId, admin, adminRow, dev, devRow } = await org();
    try {
        const input = {
            organizationId: orgId,
            accountId: dev.account_id as string,
            actorAccountId: admin.account_id as string,
            lockActorAsManager: true,
        };

        adminRow.role = 'developer';
        await assert.rejects(changeMemberRole({ ...input, newRole: 'viewer' }), {
            statusCode: 403,
        });
        await assert.rejects(removeMember(input), { statusCode: 403 });

        adminRow.role = 'admin';
        adminRow.removed_at = new Date();
        await assert.rejects(changeMemberRole({ ...input, newRole: 'viewer' }), {
            statusCode: 404,
        });
        await assert.rejects(removeMember(input), { statusCode: 404 });

        assert.equal(devRow.role, 'developer');
        assert.equal(devRow.removed_at, null);
        assert.equal(h.state.published.length, 0);

        adminRow.removed_at = null;
        await changeMemberRole({ ...input, newRole: 'viewer' });
        assert.equal(devRow.role, 'viewer');
    } finally {
        await h.close();
    }
});

test('legacy invite register caps and sanitizes user_name at the source', () => {
    const base = {
        email: 'a@example.com',
        password: 'pw-for-test-only',
        infra_id: '11111111-1111-4111-8111-111111111111',
        role: 'user',
    };
    const parse = (user_name: string) => registerSchema.safeParse({ body: { ...base, user_name } });

    const long = parse('n'.repeat(200));
    assert.ok(long.success && long.data.body.user_name === 'n'.repeat(64));
    const emailLike = parse('bob@corp.example');
    assert.ok(emailLike.success && emailLike.data.body.user_name === 'bob corp example');
    assert.equal(parse('n'.repeat(201)).success, false);
    assert.equal(parse('ab').success, false);
    assert.equal(parse('@@//::').success, false);
});

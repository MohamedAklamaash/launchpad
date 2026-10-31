import { test } from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';

const { createHarness } = await import('@/test-support/org-invitation-harness');
const { personalOrgId } = await import('@/utils/org-id');

type Harness = Awaited<ReturnType<typeof createHarness>>;
type Credential = ReturnType<Harness['addCredential']>;

const ROLES = ['owner', 'admin', 'developer', 'viewer'] as const;
type Role = (typeof ROLES)[number];

interface Fixture {
    h: Harness;
    orgId: string;
    owner: Credential;
    caller: Credential;
    target: Credential;
    targetRow: ReturnType<Harness['addMembership']>;
}

// One org with an owner; the caller and target hold the requested roles. A role of `owner`
// means that party IS the owner, so caller === target when both are owners (a self action).
const fixture = async (callerRole: Role, targetRole: Role): Promise<Fixture> => {
    const h = await createHarness();
    const owner = h.addCredential('users');
    h.addAccount(owner);
    const orgId = h.addOrg('Acme', owner.account_id as string);
    h.addMembership(orgId, owner.account_id as string, 'owner');

    const join = (role: Role): Credential => {
        if (role === 'owner') return owner;
        const credential = h.addCredential('invitedUsers');
        h.addAccount(credential);
        h.addMembership(orgId, credential.account_id as string, role);
        return credential;
    };
    const caller = join(callerRole);
    const target = join(targetRole);
    const targetRow = h.state.memberships.find(
        (m) => m.organization_id === orgId && m.account_id === target.account_id,
    ) as ReturnType<Harness['addMembership']>;
    return { h, orgId, owner, caller, target, targetRow };
};

const patchRole = (f: Fixture, accountId: string, role: string, bearer = f.h.tokenFor(f.caller)) =>
    f.h.call('PATCH', `/organizations/${f.orgId}/members/${accountId}`, { bearer, body: { role } });

const del = (f: Fixture, accountId: string, bearer = f.h.tokenFor(f.caller)) =>
    f.h.call('DELETE', `/organizations/${f.orgId}/members/${accountId}`, { bearer });

const isSelf = (f: Fixture) => f.caller.account_id === f.target.account_id;

test('change role: every caller role against every target role', async () => {
    for (const callerRole of ROLES) {
        for (const targetRole of ROLES) {
            const f = await fixture(callerRole, targetRole);
            try {
                const before = f.targetRow.role;
                const res = await patchRole(f, f.target.account_id as string, 'viewer');
                const label = `${callerRole} -> ${targetRole}`;

                if (callerRole === 'developer' || callerRole === 'viewer') {
                    assert.equal(res.status, 403, label);
                    assert.equal(f.targetRow.role, before, label);
                } else if (targetRole === 'owner') {
                    assert.equal(res.status, 409, label);
                    assert.equal(f.targetRow.role, 'owner', label);
                } else {
                    assert.equal(res.status, 200, label);
                    assert.equal(res.body?.role, 'viewer', label);
                    assert.equal(f.targetRow.role, 'viewer', label);
                }
            } finally {
                await f.h.close();
            }
        }
    }
});

test('change role: nobody can grant owner, at the edge or by any other spelling', async () => {
    for (const callerRole of ['owner', 'admin'] as const) {
        const f = await fixture(callerRole, 'developer');
        try {
            for (const role of ['owner', 'OWNER', 'superuser', '', 7]) {
                const res = await patchRole(f, f.target.account_id as string, role as string);
                assert.equal(
                    res.status,
                    role === 'owner' ? 409 : 422,
                    `${callerRole} ${String(role)}`,
                );
            }
            const extra = await f.h.call(
                'PATCH',
                `/organizations/${f.orgId}/members/${f.target.account_id}`,
                {
                    bearer: f.h.tokenFor(f.caller),
                    body: { role: 'admin', organization_id: randomUUID() },
                },
            );
            assert.equal(extra.status, 422);
            assert.equal(f.targetRow.role, 'developer');
            assert.equal(f.h.state.memberships.filter((m) => m.role === 'owner').length, 1);
        } finally {
            await f.h.close();
        }
    }
});

test('change role: an admin cannot promote themselves past admin or touch the owner', async () => {
    const f = await fixture('admin', 'owner');
    try {
        const selfToOwner = await patchRole(f, f.caller.account_id as string, 'owner');
        const demoteOwner = await patchRole(f, f.owner.account_id as string, 'admin');

        assert.equal(selfToOwner.status, 409);
        assert.equal(demoteOwner.status, 409);
        assert.equal(f.targetRow.role, 'owner');
    } finally {
        await f.h.close();
    }
});

test('change role: success writes one audit row and one membership.changed event with a bumped version', async () => {
    const f = await fixture('admin', 'developer');
    try {
        const res = await patchRole(f, f.target.account_id as string, 'admin');

        assert.equal(res.status, 200);
        assert.equal(res.body?.version, 2);
        const audit = f.h.state.audit.filter((a) => a.action === 'membership.role_changed');
        assert.deepEqual(
            audit.map((a) => [a.actor_account_id, a.target_account_id, a.old_role, a.new_role]),
            [[f.caller.account_id, f.target.account_id, 'developer', 'admin']],
        );
        assert.deepEqual(
            f.h.state.published.map((p) => [p.routingKey, p.payload.account_id, p.payload.role]),
            [['org.membership.changed', f.target.account_id, 'admin']],
        );
    } finally {
        await f.h.close();
    }
});

test('change role: an unknown or removed target is 404 and changes nothing', async () => {
    const f = await fixture('owner', 'developer');
    try {
        f.targetRow.removed_at = new Date();
        const removed = await patchRole(f, f.target.account_id as string, 'admin');
        const unknown = await patchRole(f, randomUUID(), 'admin');

        assert.equal(removed.status, 404);
        assert.equal(unknown.status, 404);
        assert.equal(f.targetRow.role, 'developer');
        assert.equal(f.h.state.published.length, 0);
    } finally {
        await f.h.close();
    }
});

test('remove: every caller role against every other target role', async () => {
    for (const callerRole of ROLES) {
        for (const targetRole of ROLES) {
            const f = await fixture(callerRole, targetRole);
            try {
                if (isSelf(f)) continue;
                const res = await del(f, f.target.account_id as string);
                const label = `${callerRole} -> ${targetRole}`;

                if (callerRole === 'developer' || callerRole === 'viewer') {
                    assert.equal(res.status, 403, label);
                    assert.equal(f.targetRow.removed_at, null, label);
                } else if (targetRole === 'owner') {
                    assert.equal(res.status, 409, label);
                    assert.equal(f.targetRow.removed_at, null, label);
                } else {
                    assert.equal(res.status, 204, label);
                    assert.notEqual(f.targetRow.removed_at, null, label);
                }
            } finally {
                await f.h.close();
            }
        }
    }
});

test('self-leave: owner gets 409 and must transfer; every other role can leave', async () => {
    for (const role of ROLES) {
        const f = await fixture(role, role);
        try {
            const self = role === 'owner' ? f.owner : f.caller;
            const row = f.h.state.memberships.find(
                (m) => m.organization_id === f.orgId && m.account_id === self.account_id,
            )!;
            const res = await del(f, self.account_id as string, f.h.tokenFor(self));

            if (role === 'owner') {
                assert.equal(res.status, 409);
                assert.equal(row.removed_at, null);
                assert.equal(f.h.state.published.length, 0);
            } else {
                assert.equal(res.status, 204, role);
                assert.notEqual(row.removed_at, null);
            }
        } finally {
            await f.h.close();
        }
    }
});

test('remove: refresh tokens and pending invitations of the removed member die with them, and the removal is published and audited', async () => {
    const f = await fixture('admin', 'developer');
    try {
        f.h.state.invitations.push({
            id: randomUUID(),
            organization_id: f.orgId,
            email: f.target.email,
            role: 'viewer',
            accepted_at: null,
            revoked_at: null,
            expires_at: new Date(Date.now() + 1000 * 60),
        });

        const res = await del(f, f.target.account_id as string);

        assert.equal(res.status, 204);
        assert.deepEqual(f.h.state.refreshRevokedFor, [f.target.id]);
        assert.notEqual(f.h.state.invitations[0]!.revoked_at, null);
        assert.deepEqual(
            f.h.state.published.map((p) => [p.routingKey, p.payload.account_id]),
            [['org.membership.removed', f.target.account_id]],
        );
        assert.equal(f.h.state.audit.filter((a) => a.action === 'membership.removed').length, 1);
    } finally {
        await f.h.close();
    }
});

test('a removed member is a stranger: 404 on everything, and cannot be removed twice', async () => {
    const f = await fixture('owner', 'admin');
    try {
        f.targetRow.removed_at = new Date();
        const bearer = f.h.tokenFor(f.target);
        const asTarget = [
            await f.h.call('GET', `/organizations/${f.orgId}/members`, { bearer }),
            await f.h.call('PATCH', `/organizations/${f.orgId}`, { bearer, body: { name: 'X' } }),
            await patchRole(f, f.owner.account_id as string, 'admin', bearer),
            await del(f, f.owner.account_id as string, bearer),
        ];
        for (const res of asTarget) assert.equal(res.status, 404);

        const again = await del(f, f.target.account_id as string, f.h.tokenFor(f.owner));
        assert.equal(again.status, 404);
        assert.equal(f.h.state.published.length, 0);
    } finally {
        await f.h.close();
    }
});

test('personal org: the owner cannot leave or be removed and keeps the owner role, but other members can be managed and can leave', async () => {
    const h = await createHarness();
    try {
        const owner = h.addCredential('users');
        const home = personalOrgId(owner.id);
        h.addOrg('Home', owner.account_id as string, home);
        const ownerRow = h.addMembership(home, owner.account_id as string, 'owner');
        const guest = h.addCredential('invitedUsers');
        const guestRow = h.addMembership(home, guest.account_id as string, 'admin');
        const peer = h.addCredential('invitedUsers');
        h.addMembership(home, peer.account_id as string, 'developer');

        const leave = await h.call('DELETE', `/organizations/${home}/members/${owner.account_id}`, {
            bearer: h.tokenFor(owner),
        });
        const removeByAdmin = await h.call(
            'DELETE',
            `/organizations/${home}/members/${owner.account_id}`,
            { bearer: h.tokenFor(guest) },
        );
        const demote = await h.call('PATCH', `/organizations/${home}/members/${owner.account_id}`, {
            bearer: h.tokenFor(guest),
            body: { role: 'viewer' },
        });
        assert.deepEqual([leave.status, removeByAdmin.status, demote.status], [409, 409, 409]);
        assert.equal(ownerRow.role, 'owner');
        assert.equal(ownerRow.removed_at, null);

        const promote = await h.call('PATCH', `/organizations/${home}/members/${peer.account_id}`, {
            bearer: h.tokenFor(owner),
            body: { role: 'viewer' },
        });
        const guestLeaves = await h.call(
            'DELETE',
            `/organizations/${home}/members/${guest.account_id}`,
            {
                bearer: h.tokenFor(guest),
            },
        );
        assert.equal(promote.status, 200);
        assert.equal(guestLeaves.status, 204);
        assert.notEqual(guestRow.removed_at, null);
    } finally {
        await h.close();
    }
});

test('cross-org: an admin of org A gets the missing-org 404 on every member route of org B, and nothing in B changes', async () => {
    const h = await createHarness();
    try {
        const adminA = h.addCredential('users');
        const orgA = h.addOrg('A', adminA.account_id as string);
        h.addMembership(orgA, adminA.account_id as string, 'admin');

        const ownerB = h.addCredential('users');
        const orgB = h.addOrg('B', ownerB.account_id as string);
        const ownerRow = h.addMembership(orgB, ownerB.account_id as string, 'owner');
        const devB = h.addCredential('invitedUsers');
        const devRow = h.addMembership(orgB, devB.account_id as string, 'developer');

        const bearer = h.tokenFor(adminA);
        const missing = randomUUID();
        const probes: [string, (org: string) => Promise<{ status: number; body: unknown }>][] = [
            ['list', (org) => h.call('GET', `/organizations/${org}/members`, { bearer })],
            [
                'patch',
                (org) =>
                    h.call('PATCH', `/organizations/${org}/members/${devB.account_id}`, {
                        bearer,
                        body: { role: 'admin' },
                    }),
            ],
            [
                'delete',
                (org) =>
                    h.call('DELETE', `/organizations/${org}/members/${devB.account_id}`, {
                        bearer,
                    }),
            ],
        ];
        for (const [name, run] of probes) {
            const real = await run(orgB);
            const absent = await run(missing);
            assert.equal(real.status, 404, name);
            assert.deepEqual(real.body, absent.body, name);
        }
        assert.equal(devRow.role, 'developer');
        assert.equal(devRow.removed_at, null);
        assert.equal(ownerRow.removed_at, null);
        assert.equal(h.state.published.length, 0);
    } finally {
        await h.close();
    }
});

test('a member of A cannot name a member of B through org A: the target must be a member of the path org', async () => {
    const h = await createHarness();
    try {
        const ownerA = h.addCredential('users');
        const orgA = h.addOrg('A', ownerA.account_id as string);
        h.addMembership(orgA, ownerA.account_id as string, 'owner');
        const ownerB = h.addCredential('users');
        const orgB = h.addOrg('B', ownerB.account_id as string);
        h.addMembership(orgB, ownerB.account_id as string, 'owner');
        const victim = h.addCredential('invitedUsers');
        const victimRow = h.addMembership(orgB, victim.account_id as string, 'developer');

        const removed = await h.call(
            'DELETE',
            `/organizations/${orgA}/members/${victim.account_id}`,
            {
                bearer: h.tokenFor(ownerA),
            },
        );
        const changed = await h.call(
            'PATCH',
            `/organizations/${orgA}/members/${victim.account_id}`,
            {
                bearer: h.tokenFor(ownerA),
                body: { role: 'admin' },
            },
        );

        assert.equal(removed.status, 404);
        assert.equal(changed.status, 404);
        assert.equal(victimRow.role, 'developer');
        assert.equal(victimRow.removed_at, null);
    } finally {
        await h.close();
    }
});

test('members list: owner, admin and developer see emails; a viewer sees names and roles only', async () => {
    for (const role of ROLES) {
        const f = await fixture(role, 'viewer');
        try {
            const res = await f.h.call('GET', `/organizations/${f.orgId}/members`, {
                bearer: f.h.tokenFor(role === 'owner' ? f.owner : f.caller),
            });

            assert.equal(res.status, 200, role);
            const members = res as unknown as { body: Record<string, unknown>[]; text: string };
            assert.equal(members.body.length, role === 'owner' ? 2 : 3);
            for (const member of members.body) {
                assert.ok(member.id && member.display_name && member.role && member.joined_at);
                assert.equal('email' in member, role !== 'viewer', role);
                const isOwnRow =
                    member.id ===
                    f.h.state.memberships.find(
                        (m) => m.account_id === (role === 'owner' ? f.owner : f.caller).account_id,
                    )?.id;
                assert.equal('account_id' in member, role !== 'viewer' || isOwnRow, role);
                if (role === 'viewer' && isOwnRow) {
                    assert.equal(member.account_id, f.caller.account_id);
                }
            }
            if (role === 'viewer') {
                const derived = [
                    f.owner.account_id as string,
                    f.target.account_id as string,
                    ...[f.owner, f.caller, f.target].map((c) => c.email),
                ];
                for (const value of derived) assert.ok(!members.text.includes(value), value);
                assert.ok(!members.text.includes('email'));
                assert.equal(members.body.filter((m) => 'account_id' in m).length, 1);
                const ids = f.h.state.memberships.map((m) => m.id);
                assert.deepEqual(members.body.map((m) => m.id).sort(), [...ids].sort());
            }
        } finally {
            await f.h.close();
        }
    }
});

test('members list: only active members of this org appear, and a missing Account gets a neutral name rather than the email', async () => {
    const h = await createHarness();
    try {
        const owner = h.addCredential('users');
        h.addAccount(owner);
        const orgId = h.addOrg('Acme', owner.account_id as string);
        h.addMembership(orgId, owner.account_id as string, 'owner');
        const gone = h.addCredential('invitedUsers');
        h.addAccount(gone);
        h.addMembership(orgId, gone.account_id as string, 'developer', new Date());
        const noAccount = h.addCredential('invitedUsers');
        h.addMembership(orgId, noAccount.account_id as string, 'viewer');
        const elsewhere = h.addCredential('invitedUsers');
        h.addAccount(elsewhere);
        h.addMembership(randomUUID(), elsewhere.account_id as string, 'admin');

        const viewerView = await h.call('GET', `/organizations/${orgId}/members`, {
            bearer: h.tokenFor(noAccount),
        });
        const ownerView = await h.call('GET', `/organizations/${orgId}/members`, {
            bearer: h.tokenFor(owner),
        });

        const names = (res: typeof ownerView) =>
            (res.body as unknown as { display_name: string }[]).map((m) => m.display_name);
        assert.deepEqual(names(ownerView), ['tester', 'Member']);
        assert.deepEqual(names(viewerView), ['tester', 'Member']);
        assert.ok(!ownerView.text.includes(noAccount.email));
        assert.ok(!ownerView.text.includes(gone.email));
        assert.ok(!ownerView.text.includes(elsewhere.email));
    } finally {
        await h.close();
    }
});

test('a non-member and a caller with no verified credential cannot list members', async () => {
    const f = await fixture('owner', 'viewer');
    try {
        const outsider = f.h.addCredential('users');
        const unverified = f.h.addCredential('invitedUsers', { email_verified_at: null });
        f.h.addMembership(f.orgId, unverified.account_id as string, 'admin');

        const missing = await f.h.call('GET', `/organizations/${randomUUID()}/members`, {
            bearer: f.h.tokenFor(outsider),
        });
        const asOutsider = await f.h.call('GET', `/organizations/${f.orgId}/members`, {
            bearer: f.h.tokenFor(outsider),
        });
        const asUnverified = await f.h.call('GET', `/organizations/${f.orgId}/members`, {
            bearer: f.h.tokenFor(unverified),
        });

        assert.equal(asOutsider.status, 404);
        assert.deepEqual(asOutsider.body, missing.body);
        assert.equal(asUnverified.status, 404);
    } finally {
        await f.h.close();
    }
});

test('path ids must be UUIDs: malformed org or account ids are rejected before any lookup', async () => {
    const f = await fixture('owner', 'developer');
    try {
        const bearer = f.h.tokenFor(f.owner);
        const bad = [
            ['GET', '/organizations/not-a-uuid/members'],
            ['PATCH', `/organizations/${f.orgId}/members/not-a-uuid`],
            ['DELETE', `/organizations/${f.orgId}/members/not-a-uuid`],
            ['DELETE', `/organizations/${f.orgId}/members/${f.owner.account_id}x`],
        ] as const;
        for (const [method, path] of bad) {
            const res = await f.h.call(method, path, {
                bearer,
                body: method === 'PATCH' ? { role: 'viewer' } : undefined,
            });
            assert.equal(res.status, 422, `${method} ${path}`);
        }
        assert.equal(f.targetRow.removed_at, null);
    } finally {
        await f.h.close();
    }
});

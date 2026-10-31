import { test } from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';

const { createHarness } = await import('@/test-support/org-invitation-harness');
const { personalOrgId } = await import('@/utils/org-id');

type Harness = Awaited<ReturnType<typeof createHarness>>;

const nowSeconds = () => Math.floor(Date.now() / 1000);

const transfer = (h: Harness, orgId: string, bearer: string, body: unknown) =>
    h.call('POST', `/organizations/${orgId}/transfer-ownership`, { bearer, body });

const setup = async () => {
    const h = await createHarness();
    const owner = h.addCredential('users');
    const orgId = h.addOrg('Acme', owner.account_id as string);
    const ownerRow = h.addMembership(orgId, owner.account_id as string, 'owner');
    const target = h.addCredential('invitedUsers');
    const targetRow = h.addMembership(orgId, target.account_id as string, 'developer');
    const fresh = () => h.tokenFor(owner, { auth_time: nowSeconds() });
    return { h, owner, orgId, ownerRow, target, targetRow, fresh };
};

const activeOwners = (h: Harness, orgId: string) =>
    h.state.memberships.filter(
        (m) => m.organization_id === orgId && m.role === 'owner' && m.removed_at === null,
    );

test('owner transfers: target becomes owner, caller becomes admin, one owner remains, org owner updated', async () => {
    const { h, owner, orgId, ownerRow, target, targetRow, fresh } = await setup();
    try {
        const res = await transfer(h, orgId, fresh(), { new_owner_account_id: target.account_id });

        assert.equal(res.status, 200);
        assert.equal(res.body?.owner_account_id, target.account_id);
        assert.equal(res.body?.previous_owner_account_id, owner.account_id);
        assert.equal(res.body?.previous_owner_role, 'admin');
        assert.equal(ownerRow.role, 'admin');
        assert.equal(targetRow.role, 'owner');
        assert.equal(ownerRow.version, 2);
        assert.equal(targetRow.version, 2);
        assert.deepEqual(
            activeOwners(h, orgId).map((m) => m.account_id),
            [target.account_id],
        );
        assert.equal(h.state.orgs[0]!.owner_account_id, target.account_id);
    } finally {
        await h.close();
    }
});

test('demote runs before promote inside the transaction, so the one-owner index is never violated', async () => {
    const { h, orgId, ownerRow, target, targetRow, fresh } = await setup();
    try {
        await transfer(h, orgId, fresh(), { new_owner_account_id: target.account_id });

        assert.deepEqual(
            h.state.queryLog.map((q) => [q.membershipId, q.role]),
            [
                [ownerRow.id, 'admin'],
                [targetRow.id, 'owner'],
            ],
        );
    } finally {
        await h.close();
    }
});

test('two audit rows are written, one per membership, attributed to the caller', async () => {
    const { h, owner, orgId, target, fresh } = await setup();
    try {
        await transfer(h, orgId, fresh(), { new_owner_account_id: target.account_id });

        assert.deepEqual(
            h.state.audit.map((a) => [
                a.action,
                a.actor_account_id,
                a.target_account_id,
                a.old_role,
                a.new_role,
            ]),
            [
                [
                    'membership.ownership_demoted',
                    owner.account_id,
                    owner.account_id,
                    'owner',
                    'admin',
                ],
                [
                    'membership.ownership_promoted',
                    owner.account_id,
                    target.account_id,
                    'developer',
                    'owner',
                ],
            ],
        );
    } finally {
        await h.close();
    }
});

test('events publish after commit, demote first, each carrying that membership bumped version', async () => {
    const { h, owner, orgId, target, fresh } = await setup();
    try {
        await transfer(h, orgId, fresh(), { new_owner_account_id: target.account_id });

        assert.deepEqual(h.state.published, [
            {
                routingKey: 'org.membership.changed',
                payload: {
                    organization_id: orgId,
                    account_id: owner.account_id,
                    role: 'admin',
                    version: 2,
                },
            },
            {
                routingKey: 'org.membership.changed',
                payload: {
                    organization_id: orgId,
                    account_id: target.account_id,
                    role: 'owner',
                    version: 2,
                    previous_owner_account_id: owner.account_id,
                    previous_owner_version: 2,
                },
            },
        ]);
    } finally {
        await h.close();
    }
});

test("the demoted owner's refresh tokens are revoked, the new owner's are not", async () => {
    const { h, owner, target, orgId, fresh } = await setup();
    try {
        await transfer(h, orgId, fresh(), { new_owner_account_id: target.account_id });

        assert.deepEqual(h.state.refreshRevokedFor, [owner.id]);
        assert.ok(!h.state.refreshRevokedFor.includes(target.id));
    } finally {
        await h.close();
    }
});

test('a failure mid-transaction leaves both roles, the org owner, tokens and events untouched', async () => {
    const { h, owner, orgId, ownerRow, target, targetRow, fresh } = await setup();
    try {
        h.state.auditFailOnCall = 2;
        const res = await transfer(h, orgId, fresh(), { new_owner_account_id: target.account_id });

        assert.equal(res.status, 500);
        assert.equal(ownerRow.role, 'owner');
        assert.equal(targetRow.role, 'developer');
        assert.equal(ownerRow.version, 1);
        assert.equal(targetRow.version, 1);
        assert.equal(h.state.orgs[0]!.owner_account_id, owner.account_id);
        assert.equal(h.state.audit.length, 0);
        assert.equal(h.state.refreshRevokedFor.length, 0);
        assert.equal(h.state.published.length, 0);
    } finally {
        await h.close();
    }
});

test('a state change that rolls back leaves no outbox row, so nothing is ever published for it', async () => {
    const { h, orgId, ownerRow, target, targetRow, fresh } = await setup();
    try {
        h.state.outboxFailOnCall = 2;
        const res = await transfer(h, orgId, fresh(), { new_owner_account_id: target.account_id });

        assert.equal(res.status, 500);
        assert.equal(h.state.outbox.length, 0);
        assert.equal(h.state.published.length, 0);
        assert.equal(ownerRow.role, 'owner');
        assert.equal(targetRow.role, 'developer');
    } finally {
        await h.close();
    }
});

test('the promote and demote reach the outbox in that transaction, demote first', async () => {
    const { h, orgId, owner, target, fresh } = await setup();
    try {
        await transfer(h, orgId, fresh(), { new_owner_account_id: target.account_id });

        assert.deepEqual(
            h.state.outbox.map((row) => [row.payload.account_id, row.payload.role]),
            [
                [owner.account_id, 'admin'],
                [target.account_id, 'owner'],
            ],
        );
        assert.equal(h.state.outbox[0]!.payload.previous_owner_account_id, undefined);
        assert.equal(h.state.outbox[1]!.payload.previous_owner_account_id, owner.account_id);
        assert.equal(h.state.outbox[1]!.payload.previous_owner_version, 2);
    } finally {
        await h.close();
    }
});

test('a target Account shared by two credential rows is refused', async () => {
    const { h, orgId, ownerRow, target, fresh } = await setup();
    try {
        h.addCredential('users', { account_id: target.account_id });
        const res = await transfer(h, orgId, fresh(), { new_owner_account_id: target.account_id });

        assert.equal(res.status, 409);
        assert.equal(ownerRow.role, 'owner');
        assert.equal(h.state.outbox.length, 0);
    } finally {
        await h.close();
    }
});

test('a stale, missing or future auth_time is refused with 401 reauth_required and changes nothing', async () => {
    const { h, owner, orgId, ownerRow, target, fresh } = await setup();
    try {
        const body = { new_owner_account_id: target.account_id };
        const tokens = [
            h.tokenFor(owner, { auth_time: nowSeconds() - 3600 }),
            h.tokenFor(owner, { auth_time: nowSeconds() + 3600 }),
            h.tokenFor(owner),
        ];
        for (const bearer of tokens) {
            const res = await transfer(h, orgId, bearer, body);
            assert.equal(res.status, 401);
            assert.deepEqual(res.body, {
                message: 'Please log in again to continue',
                code: 'reauth_required',
            });
        }
        assert.equal(ownerRow.role, 'owner');
        assert.equal(h.state.published.length, 0);

        const ok = await transfer(h, orgId, fresh(), body);
        assert.equal(ok.status, 200);
    } finally {
        await h.close();
    }
});

test('the freshness window is the exit flow window: 600s passes, 700s does not', async () => {
    const { h, owner, orgId, target } = await setup();
    try {
        const body = { new_owner_account_id: target.account_id };
        const justOutside = h.tokenFor(owner, { auth_time: nowSeconds() - 700 });
        assert.equal((await transfer(h, orgId, justOutside, body)).status, 401);

        const justInside = h.tokenFor(owner, { auth_time: nowSeconds() - 590 });
        assert.equal((await transfer(h, orgId, justInside, body)).status, 200);
    } finally {
        await h.close();
    }
});

test('an admin, a developer and a viewer get 403 even with a fresh login, and nothing changes', async () => {
    const { h, orgId, ownerRow, target } = await setup();
    try {
        for (const role of ['admin', 'developer', 'viewer']) {
            const member = h.addCredential('invitedUsers');
            h.addMembership(orgId, member.account_id as string, role);
            const bearer = h.tokenFor(member, { auth_time: nowSeconds() });
            const res = await transfer(h, orgId, bearer, {
                new_owner_account_id: target.account_id,
            });
            assert.equal(res.status, 403, role);
        }
        assert.equal(ownerRow.role, 'owner');
        assert.equal(h.state.published.length, 0);
        assert.equal(h.state.audit.length, 0);
    } finally {
        await h.close();
    }
});

test('a non-member and a removed member get the same 404 as a missing org', async () => {
    const { h, orgId, target } = await setup();
    try {
        const body = { new_owner_account_id: target.account_id };
        const stranger = h.addCredential('users');
        const strangerRes = await transfer(
            h,
            orgId,
            h.tokenFor(stranger, { auth_time: nowSeconds() }),
            body,
        );
        const former = h.addCredential('users');
        h.addMembership(orgId, former.account_id as string, 'owner', new Date());
        const formerRes = await transfer(
            h,
            orgId,
            h.tokenFor(former, { auth_time: nowSeconds() }),
            body,
        );
        const missingRes = await transfer(
            h,
            randomUUID(),
            h.tokenFor(stranger, { auth_time: nowSeconds() }),
            body,
        );

        for (const res of [strangerRes, formerRes, missingRes]) {
            assert.equal(res.status, 404);
        }
        assert.deepEqual(strangerRes.body, missingRes.body);
    } finally {
        await h.close();
    }
});

test('a token whose account claim does not match its credential row is refused', async () => {
    const { h, owner, orgId, target } = await setup();
    try {
        const forged = h.tokenFor(owner, { auth_time: nowSeconds(), account_id: randomUUID() });
        const res = await transfer(h, orgId, forged, { new_owner_account_id: target.account_id });

        assert.equal(res.status, 404);
    } finally {
        await h.close();
    }
});

test('the target must be an active member with a verified account; transfer to self is refused', async () => {
    const { h, owner, orgId, ownerRow, fresh } = await setup();
    try {
        const outsider = h.addCredential('invitedUsers');
        const removed = h.addCredential('invitedUsers');
        h.addMembership(orgId, removed.account_id as string, 'admin', new Date());
        const unverified = h.addCredential('invitedUsers', { email_verified_at: null });
        h.addMembership(orgId, unverified.account_id as string, 'admin');
        const noCredentialAccount = randomUUID();
        h.addMembership(orgId, noCredentialAccount, 'admin');

        const targets = [
            outsider.account_id,
            removed.account_id,
            unverified.account_id,
            noCredentialAccount,
            owner.account_id,
            randomUUID(),
        ];
        for (const accountId of targets) {
            const res = await transfer(h, orgId, fresh(), { new_owner_account_id: accountId });
            assert.equal(res.status, 409, String(accountId));
        }
        assert.equal(ownerRow.role, 'owner');
        assert.equal(h.state.published.length, 0);
        assert.equal(h.state.refreshRevokedFor.length, 0);
    } finally {
        await h.close();
    }
});

test('a malformed target id is rejected at the edge', async () => {
    const { h, orgId, fresh } = await setup();
    try {
        const res = await transfer(h, orgId, fresh(), { new_owner_account_id: 'not-a-uuid' });

        assert.equal(res.status, 422);
        assert.equal(h.state.published.length, 0);
    } finally {
        await h.close();
    }
});

test('a personal organization cannot be transferred, and the refusal changes nothing', async () => {
    const h = await createHarness();
    try {
        const owner = h.addCredential('users');
        const orgId = h.addOrg('Personal', owner.account_id as string, personalOrgId(owner.id));
        const ownerRow = h.addMembership(orgId, owner.account_id as string, 'owner');
        const target = h.addCredential('invitedUsers');
        h.addMembership(orgId, target.account_id as string, 'admin');

        const res = await transfer(h, orgId, h.tokenFor(owner, { auth_time: nowSeconds() }), {
            new_owner_account_id: target.account_id,
        });

        assert.equal(res.status, 409);
        assert.equal(res.body?.message, 'A personal organization cannot change owner');
        assert.equal(ownerRow.role, 'owner');
        assert.equal(h.state.orgs[0]!.owner_account_id, owner.account_id);
        assert.equal(h.state.published.length, 0);
        assert.equal(h.state.audit.length, 0);
    } finally {
        await h.close();
    }
});

test('an org whose owner column disagrees with the owner membership fails closed', async () => {
    const { h, orgId, target, fresh } = await setup();
    try {
        h.state.orgs[0]!.owner_account_id = randomUUID();
        const res = await transfer(h, orgId, fresh(), { new_owner_account_id: target.account_id });

        assert.equal(res.status, 409);
        assert.equal(h.state.published.length, 0);
    } finally {
        await h.close();
    }
});

test('after the transfer the former owner is an admin and cannot transfer, but the new owner can hand it back', async () => {
    const { h, owner, orgId, ownerRow, target, targetRow, fresh } = await setup();
    try {
        await transfer(h, orgId, fresh(), { new_owner_account_id: target.account_id });

        const again = await transfer(h, orgId, fresh(), {
            new_owner_account_id: target.account_id,
        });
        assert.equal(again.status, 403);

        const back = await transfer(h, orgId, h.tokenFor(target, { auth_time: nowSeconds() }), {
            new_owner_account_id: owner.account_id,
        });
        assert.equal(back.status, 200);
        assert.equal(ownerRow.role, 'owner');
        assert.equal(targetRow.role, 'admin');
        assert.equal(ownerRow.version, 3);
        assert.equal(targetRow.version, 3);
        assert.equal(h.state.orgs[0]!.owner_account_id, owner.account_id);
        assert.equal(activeOwners(h, orgId).length, 1);
    } finally {
        await h.close();
    }
});

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { HttpError } from '@launchpad/common';

const { createHarness } = await import('@/test-support/org-invitation-harness');
const { changeMemberRole, removeMember } =
    await import('@/service/organizations/membership.service');

type Harness = Awaited<ReturnType<typeof createHarness>>;

const setup = async () => {
    const h = await createHarness();
    const owner = h.addCredential('users');
    const orgId = h.addOrg('Acme', owner.account_id as string);
    const ownerRow = h.addMembership(orgId, owner.account_id as string, 'owner');
    const admin = h.addCredential('invitedUsers');
    h.addMembership(orgId, admin.account_id as string, 'admin');
    return { h, owner, admin, orgId, ownerRow };
};

const assertConflictAndUntouched = async (
    h: Harness,
    ownerRow: { role: unknown; version: unknown; removed_at: unknown },
    action: () => Promise<unknown>,
) => {
    await assert.rejects(action, (error: unknown) => {
        assert.ok(error instanceof HttpError);
        assert.equal(error.statusCode, 409);
        return true;
    });
    assert.equal(ownerRow.role, 'owner');
    assert.equal(ownerRow.version, 1);
    assert.equal(ownerRow.removed_at, null);
    assert.equal(h.state.audit.length, 0);
    assert.equal(h.state.published.length, 0);
    assert.equal(h.state.refreshRevokedFor.length, 0);
};

test('the owner cannot demote themselves: 409 and no write', async () => {
    const { h, owner, orgId, ownerRow } = await setup();
    try {
        await assertConflictAndUntouched(h, ownerRow, () =>
            changeMemberRole({
                organizationId: orgId,
                accountId: owner.account_id as string,
                newRole: 'admin',
                actorAccountId: owner.account_id as string,
            }),
        );
    } finally {
        await h.close();
    }
});

test('an admin cannot demote or remove the owner: 409 and no write', async () => {
    const { h, admin, owner, orgId, ownerRow } = await setup();
    try {
        await assertConflictAndUntouched(h, ownerRow, () =>
            changeMemberRole({
                organizationId: orgId,
                accountId: owner.account_id as string,
                newRole: 'viewer',
                actorAccountId: admin.account_id,
            }),
        );
        await assertConflictAndUntouched(h, ownerRow, () =>
            removeMember({
                organizationId: orgId,
                accountId: owner.account_id as string,
                actorAccountId: admin.account_id,
            }),
        );
    } finally {
        await h.close();
    }
});

test('the owner cannot remove themselves: 409, no tombstone, no token revocation', async () => {
    const { h, owner, orgId, ownerRow } = await setup();
    try {
        await assertConflictAndUntouched(h, ownerRow, () =>
            removeMember({
                organizationId: orgId,
                accountId: owner.account_id as string,
                actorAccountId: owner.account_id as string,
            }),
        );
    } finally {
        await h.close();
    }
});

test('nobody can be promoted to owner through a role change', async () => {
    const { h, admin, orgId, ownerRow } = await setup();
    try {
        await assertConflictAndUntouched(h, ownerRow, () =>
            changeMemberRole({
                organizationId: orgId,
                accountId: admin.account_id as string,
                newRole: 'owner',
                actorAccountId: admin.account_id,
            }),
        );
    } finally {
        await h.close();
    }
});

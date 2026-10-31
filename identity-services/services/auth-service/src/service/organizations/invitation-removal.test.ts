import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash, randomBytes, randomUUID } from 'node:crypto';

const { createHarness } = await import('@/test-support/org-invitation-harness');
const { removeMember } = await import('./membership.service');
const { MAX_INVITES_PER_WINDOW, MAX_GLOBAL_EMAIL_INVITES_PER_WINDOW } =
    await import('@/utils/otp-attempts');

type Harness = Awaited<ReturnType<typeof createHarness>>;

const GENERIC = 'Invitation is invalid, expired, or was issued to a different account';

const seed = async () => {
    const h = await createHarness();
    const orgId = h.addOrg();
    const inviter = h.addCredential('users');
    h.addMembership(orgId, inviter.account_id as string, 'owner');
    const invitee = h.addCredential('invitedUsers', { email: 'invitee@example.com' });
    const token = randomBytes(32).toString('base64url');
    const invitation = {
        id: randomUUID(),
        organization_id: orgId,
        email: 'invitee@example.com',
        role: 'admin',
        invited_by_account_id: inviter.account_id,
        token_hash: createHash('sha256').update(token).digest('hex'),
        expires_at: new Date(Date.now() + 60_000),
        accepted_at: null as Date | null,
        revoked_at: null as Date | null,
        created_at: new Date(Date.now() - 120_000),
        save: async () => undefined,
    };
    h.state.invitations.push(invitation);
    return { h, orgId, inviter, invitee, token, invitation };
};

const accept = (h: Harness, token: string, bearer: string) =>
    h.call('POST', '/invitations/accept', { bearer, body: { token } });

const invite = (h: Harness, orgId: string, email: string, bearer: string) =>
    h.call('POST', `/organizations/${orgId}/invitations`, {
        bearer,
        body: { email, role: 'viewer' },
    });

test('removing a member revokes their pending invitations, so the old token cannot bring them back as admin', async () => {
    const { h, orgId, invitee, token, invitation } = await seed();
    try {
        h.addMembership(orgId, invitee.account_id as string, 'viewer');

        await removeMember({
            organizationId: orgId,
            accountId: invitee.account_id as string,
            actorAccountId: null,
        });
        const res = await accept(h, token, h.tokenFor(invitee));

        assert.ok(invitation.revoked_at);
        assert.equal(res.status, 400);
        assert.equal(res.body?.message, GENERIC);
        const row = h.state.memberships.find((m) => m.account_id === invitee.account_id)!;
        assert.ok(row.removed_at);
        assert.equal(row.role, 'viewer');
    } finally {
        await h.close();
    }
});

test('an unrevoked invitation issued before the membership was removed still cannot revive it', async () => {
    const { h, orgId, invitee, token, invitation } = await seed();
    try {
        h.addMembership(
            orgId,
            invitee.account_id as string,
            'viewer',
            new Date(Date.now() - 60_000),
        );

        const res = await accept(h, token, h.tokenFor(invitee));

        assert.equal(res.status, 400);
        assert.equal(invitation.accepted_at, null);
    } finally {
        await h.close();
    }
});

test('an invitation issued after the removal can revive the membership', async () => {
    const { h, orgId, invitee, token, invitation } = await seed();
    try {
        invitation.created_at = new Date();
        h.addMembership(
            orgId,
            invitee.account_id as string,
            'viewer',
            new Date(Date.now() - 60_000),
        );

        const res = await accept(h, token, h.tokenFor(invitee));

        assert.equal(res.status, 200);
    } finally {
        await h.close();
    }
});

test('losing the unique-index race on (org, account) is a 409, not a 500', async () => {
    const { h, invitee, token } = await seed();
    try {
        h.state.membershipCreateError = h.newUniqueError();

        const res = await accept(h, token, h.tokenFor(invitee));

        assert.equal(res.status, 409);
        assert.equal(h.state.published.length, 0);
    } finally {
        await h.close();
    }
});

test('inviting an email that already belongs to an active member is a 409 and spends no budget', async () => {
    const { h, orgId, inviter, invitee } = await seed();
    try {
        h.addMembership(orgId, invitee.account_id as string, 'viewer');

        const res = await invite(h, orgId, 'Invitee@Example.com', h.tokenFor(inviter));

        assert.equal(res.status, 409);
        assert.equal(h.state.redisCounts.size, 0);
    } finally {
        await h.close();
    }
});

test('a re-invite supersedes the older pending invitation for the same (org, email)', async () => {
    const { h, orgId, inviter, invitation } = await seed();
    try {
        const res = await invite(h, orgId, 'invitee@example.com', h.tokenFor(inviter));

        assert.equal(res.status, 201);
        assert.ok(invitation.revoked_at);
        assert.equal(h.state.invitations.at(-1)!.revoked_at, null);
    } finally {
        await h.close();
    }
});

test('per-email caps are isolated per organization and from the legacy invite flow', async () => {
    const { h, orgId, inviter } = await seed();
    try {
        const orgB = h.addOrg('B');
        const ownerB = h.addCredential('users');
        h.addMembership(orgB, ownerB.account_id as string, 'owner');
        const email = 'victim@example.com';

        for (let i = 0; i < MAX_INVITES_PER_WINDOW; i++) {
            assert.equal((await invite(h, orgId, email, h.tokenFor(inviter))).status, 201);
        }
        const blockedInA = await invite(h, orgId, email, h.tokenFor(inviter));
        const openInB = await invite(h, orgB, email, h.tokenFor(ownerB));

        assert.equal(blockedInA.status, 429);
        assert.equal(openInB.status, 201);
        const throttleKeys = [...h.state.redisCounts.keys()].filter((k) =>
            k.startsWith('invite-throttle:'),
        );
        assert.ok(!throttleKeys.includes(`invite-throttle:${email}`));
        assert.ok(!throttleKeys.some((k) => k.startsWith('invite-throttle:legacy:')));
    } finally {
        await h.close();
    }
});

test('the global per-email ceiling still stops a spray across many organizations', async () => {
    const { h } = await seed();
    try {
        let last = 0;
        for (let i = 0; i < MAX_GLOBAL_EMAIL_INVITES_PER_WINDOW + 1; i++) {
            const orgId = h.addOrg(`o${i}`);
            const owner = h.addCredential('users');
            h.addMembership(orgId, owner.account_id as string, 'owner');
            last = (await invite(h, orgId, 'victim@example.com', h.tokenFor(owner))).status;
        }
        assert.equal(last, 429);
    } finally {
        await h.close();
    }
});

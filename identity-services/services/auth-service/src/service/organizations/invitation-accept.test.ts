import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash, randomBytes, randomUUID } from 'node:crypto';

const { createHarness } = await import('@/test-support/org-invitation-harness');

type Harness = Awaited<ReturnType<typeof createHarness>>;

const sha256 = (value: string) => createHash('sha256').update(value).digest('hex');

interface SeedOptions {
    invitedEmail?: string;
    role?: string;
    expiresInMs?: number;
    inviterRole?: string | null;
}

const seed = async (opts: SeedOptions = {}) => {
    const h = await createHarness();
    const orgId = h.addOrg();
    const inviter = h.addCredential('users');
    if (opts.inviterRole !== null) {
        h.addMembership(orgId, inviter.account_id as string, opts.inviterRole ?? 'admin');
    }
    const invitedEmail = opts.invitedEmail ?? 'invitee@example.com';
    const token = randomBytes(32).toString('base64url');
    const invitation = {
        id: randomUUID(),
        organization_id: orgId,
        email: invitedEmail,
        role: opts.role ?? 'developer',
        invited_by_account_id: inviter.account_id,
        token_hash: sha256(token),
        expires_at: new Date(Date.now() + (opts.expiresInMs ?? 60_000)),
        accepted_at: null as Date | null,
        revoked_at: null as Date | null,
        created_at: new Date(),
        save: async () => undefined,
    };
    h.state.invitations.push(invitation);
    return { h, orgId, token, invitation, inviter };
};

const accept = (h: Harness, token: string, bearer?: string) =>
    h.call('POST', '/invitations/accept', { bearer, body: { token } });

const GENERIC = 'Invitation is invalid, expired, or was issued to a different account';

test('accept: a verified invitee with a matching email gets the membership, one audit row, and org.invitation.accepted after commit', async () => {
    const { h, orgId, token, invitation } = await seed();
    try {
        const invitee = h.addCredential('invitedUsers', { email: 'Invitee@Example.com' });

        const res = await accept(h, token, h.tokenFor(invitee));

        assert.equal(res.status, 200);
        assert.deepEqual(res.body, { organization_id: orgId, role: 'developer' });
        const membership = h.state.memberships.find((m) => m.account_id === invitee.account_id)!;
        assert.equal(membership.role, 'developer');
        assert.equal(membership.removed_at, null);
        assert.ok(invitation.accepted_at);
        assert.deepEqual(
            h.state.audit.map((a) => a.action),
            ['membership.added', 'invitation.accepted'],
        );
        assert.equal(h.state.published.length, 1);
        assert.equal(h.state.published[0]!.routingKey, 'org.invitation.accepted');
        assert.deepEqual(h.state.published[0]!.payload, {
            organization_id: orgId,
            account_id: invitee.account_id,
            email: 'invitee@example.com',
            role: 'developer',
            version: 1,
        });
        assert.ok(!res.text.includes(token));
    } finally {
        await h.close();
    }
});

test('accept: a GitHub user with no linked Account yet is linked through the guarded helper', async () => {
    const { h, token } = await seed();
    try {
        const invitee = h.addCredential('users', {
            email: 'invitee@example.com',
            account_id: null,
        });

        const res = await accept(h, token, h.tokenFor(invitee));

        assert.equal(res.status, 200);
        assert.equal(h.state.accounts.length, 1);
        assert.equal(invitee.account_id, h.state.accounts[0]!.id);
        assert.equal(h.state.memberships.at(-1)!.account_id, invitee.account_id);
    } finally {
        await h.close();
    }
});

test('accept: the token is single-use', async () => {
    const { h, token } = await seed();
    try {
        const invitee = h.addCredential('invitedUsers', { email: 'invitee@example.com' });
        const bearer = h.tokenFor(invitee);

        assert.equal((await accept(h, token, bearer)).status, 200);
        const again = await accept(h, token, bearer);

        assert.equal(again.status, 400);
        assert.equal(again.body?.message, GENERIC);
        assert.equal(h.state.published.length, 1);
    } finally {
        await h.close();
    }
});

test('accept: two concurrent accepts of one token yield exactly one membership and one event', async () => {
    const { h, token } = await seed();
    try {
        const invitee = h.addCredential('invitedUsers', { email: 'invitee@example.com' });
        const bearer = h.tokenFor(invitee);

        const results = await Promise.all([accept(h, token, bearer), accept(h, token, bearer)]);

        assert.deepEqual(results.map((r) => r.status).sort(), [200, 400]);
        assert.equal(
            h.state.memberships.filter((m) => m.account_id === invitee.account_id).length,
            1,
        );
        assert.equal(h.state.published.length, 1);
        assert.equal(h.state.audit.filter((a) => a.action === 'invitation.accepted').length, 1);
    } finally {
        await h.close();
    }
});

test('accept: expired, revoked, unknown and wrong-email attempts are indistinguishable and change nothing', async () => {
    const cases: {
        name: string;
        seedOpts: SeedOptions;
        prepare: (s: Awaited<ReturnType<typeof seed>>) => void;
        email: string;
        verified?: boolean;
    }[] = [
        {
            name: 'expired',
            seedOpts: { expiresInMs: -1000 },
            prepare: () => {},
            email: 'invitee@example.com',
        },
        {
            name: 'revoked',
            seedOpts: {},
            prepare: (s) => {
                s.invitation.revoked_at = new Date();
            },
            email: 'invitee@example.com',
        },
        {
            name: 'wrong email',
            seedOpts: {},
            prepare: () => {},
            email: 'someone.else@example.com',
        },
        {
            name: 'unverified credential',
            seedOpts: {},
            prepare: () => {},
            email: 'invitee@example.com',
            verified: false,
        },
        {
            name: 'inviter since removed',
            seedOpts: { inviterRole: null },
            prepare: () => {},
            email: 'invitee@example.com',
        },
        {
            name: 'inviter since demoted',
            seedOpts: { inviterRole: 'developer' },
            prepare: () => {},
            email: 'invitee@example.com',
        },
    ];

    const bodies: unknown[] = [];
    for (const c of cases) {
        const s = await seed(c.seedOpts);
        try {
            c.prepare(s);
            const caller =
                c.verified === false
                    ? s.h.addCredential('invitedUsers', { email: c.email, email_verified_at: null })
                    : s.h.addCredential('invitedUsers', { email: c.email });

            const res = await accept(s.h, s.token, s.h.tokenFor(caller));

            assert.equal(res.status, 400, c.name);
            bodies.push(res.body);
            assert.equal(
                s.h.state.memberships.filter((m) => m.account_id === caller.account_id).length,
                0,
                c.name,
            );
            assert.equal(s.h.state.published.length, 0, c.name);
        } finally {
            await s.h.close();
        }
    }

    const unknown = await seed();
    try {
        const invitee = unknown.h.addCredential('invitedUsers', { email: 'invitee@example.com' });
        const res = await accept(
            unknown.h,
            randomBytes(32).toString('base64url'),
            unknown.h.tokenFor(invitee),
        );
        assert.equal(res.status, 400);
        bodies.push(res.body);
    } finally {
        await unknown.h.close();
    }

    for (const body of bodies) assert.deepEqual(body, { message: GENERIC });
});

test('accept: a forwarded token is useless to a different account', async () => {
    const { h, token, invitation } = await seed();
    try {
        const attacker = h.addCredential('users', { email: 'attacker@example.com' });

        const res = await accept(h, token, h.tokenFor(attacker));

        assert.equal(res.status, 400);
        assert.equal(invitation.accepted_at, null);
    } finally {
        await h.close();
    }
});

test('accept: a removed member who is re-invited revives the tombstoned membership with a higher version', async () => {
    const { h, orgId, token } = await seed({ role: 'viewer' });
    try {
        const invitee = h.addCredential('invitedUsers', { email: 'invitee@example.com' });
        const tombstone = h.addMembership(orgId, invitee.account_id as string, 'admin', new Date());
        tombstone.version = 4;

        const res = await accept(h, token, h.tokenFor(invitee));

        assert.equal(res.status, 200);
        const rows = h.state.memberships.filter((m) => m.account_id === invitee.account_id);
        assert.equal(rows.length, 1);
        assert.equal(rows[0]!.id, tombstone.id);
        assert.equal(rows[0]!.removed_at, null);
        assert.equal(rows[0]!.role, 'viewer');
        assert.equal(rows[0]!.version, 5);
        assert.equal(h.state.published[0]!.payload.version, 5);
        assert.ok(h.state.audit.some((a) => a.action === 'membership.revived'));
    } finally {
        await h.close();
    }
});

test('accept: an existing active member gets 409 and the invitation stays unused', async () => {
    const { h, orgId, token, invitation } = await seed();
    try {
        const invitee = h.addCredential('invitedUsers', { email: 'invitee@example.com' });
        h.addMembership(orgId, invitee.account_id as string, 'viewer');

        const res = await accept(h, token, h.tokenFor(invitee));

        assert.equal(res.status, 409);
        assert.equal(invitation.accepted_at, null);
        assert.equal(h.state.published.length, 0);
    } finally {
        await h.close();
    }
});

test('accept: requires a session; a password-reset scoped token and a missing token are rejected', async () => {
    const { h, token } = await seed();
    try {
        const invitee = h.addCredential('invitedUsers', { email: 'invitee@example.com' });

        assert.equal((await accept(h, token)).status, 401);
        const scoped = h.tokenFor(invitee, { scope: 'password_reset' });
        assert.equal((await accept(h, token, scoped)).status, 401);
        assert.equal(h.state.memberships.length, 1);
    } finally {
        await h.close();
    }
});

test('accept: the invitation token is body-only; GET with a query token does not exist and malformed tokens are rejected', async () => {
    const { h, token } = await seed();
    try {
        const invitee = h.addCredential('invitedUsers', { email: 'invitee@example.com' });
        const bearer = h.tokenFor(invitee);

        const get = await h.call('GET', `/invitations/accept?token=${token}`, { bearer });
        assert.equal(get.status, 404);

        const malformed = await accept(h, 'short', bearer);
        assert.equal(malformed.status, 422);
        assert.equal(h.state.memberships.length, 1);
    } finally {
        await h.close();
    }
});

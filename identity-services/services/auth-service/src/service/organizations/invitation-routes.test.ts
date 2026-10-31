import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';

const { createHarness } = await import('@/test-support/org-invitation-harness');
const { MAX_INVITES_PER_WINDOW, MAX_ORG_INVITES_PER_WINDOW } = await import('@/utils/otp-attempts');

const sha256 = (value: string) => createHash('sha256').update(value).digest('hex');

const withOrg = async (callerRole: string | null) => {
    const h = await createHarness();
    const orgId = h.addOrg();
    const caller = h.addCredential('users');
    if (callerRole) h.addMembership(orgId, caller.account_id as string, callerRole);
    return { h, orgId, caller, bearer: h.tokenFor(caller) };
};

const invite = (
    orgId: string,
    body: unknown,
    bearer: string,
    h: Awaited<ReturnType<typeof createHarness>>,
) => h.call('POST', `/organizations/${orgId}/invitations`, { bearer, body });

test('an admin can invite a developer: only the hash is stored, the token goes to the email job and never to the response or the audit log', async () => {
    const { h, orgId, bearer } = await withOrg('admin');
    try {
        const res = await invite(
            orgId,
            { email: 'New.Person@Example.com', role: 'developer' },
            bearer,
            h,
        );

        assert.equal(res.status, 201);
        assert.equal(res.body?.email, 'new.person@example.com');
        assert.equal(res.body?.role, 'developer');

        const job = h.state.emailJobs[0]!;
        assert.equal(job.name, 'org-invitation-email-event');
        const token = job.data.token as string;
        assert.match(token, /^[A-Za-z0-9_-]{43}$/);
        assert.equal(h.state.invitations[0]!.token_hash, sha256(token));
        assert.deepEqual(job.opts, { removeOnComplete: true, removeOnFail: { age: 3600 } });
        assert.equal(job.data.organization_name, 'Acme <b>Corp</b>');

        assert.ok(!res.text.includes(token));
        assert.ok(!res.text.includes('token'));
        assert.ok(!JSON.stringify(h.state.audit).includes(token));
        assert.equal(h.state.audit.length, 1);
        assert.equal(h.state.audit[0]!.action, 'invitation.issued');
    } finally {
        await h.close();
    }
});

test('an owner can invite a viewer', async () => {
    const { h, orgId, bearer } = await withOrg('owner');
    try {
        const res = await invite(orgId, { email: 'v@example.com', role: 'viewer' }, bearer, h);
        assert.equal(res.status, 201);
    } finally {
        await h.close();
    }
});

test('an admin cannot invite as owner, and nothing is created or sent', async () => {
    const { h, orgId, bearer } = await withOrg('admin');
    try {
        const res = await invite(orgId, { email: 'x@example.com', role: 'owner' }, bearer, h);

        assert.equal(res.status, 409);
        assert.equal(h.state.invitations.length, 0);
        assert.equal(h.state.emailJobs.length, 0);
    } finally {
        await h.close();
    }
});

test('a developer and a viewer cannot invite (403), and a non-member gets the same 404 as a missing org', async () => {
    for (const role of ['developer', 'viewer']) {
        const { h, orgId, bearer } = await withOrg(role);
        try {
            const res = await invite(orgId, { email: 'x@example.com', role: 'viewer' }, bearer, h);
            assert.equal(res.status, 403, role);
            assert.equal(h.state.invitations.length, 0);
            assert.equal(
                (await h.call('GET', `/organizations/${orgId}/invitations`, { bearer })).status,
                403,
            );
        } finally {
            await h.close();
        }
    }

    const { h, orgId, bearer } = await withOrg(null);
    try {
        const nonMember = await invite(
            orgId,
            { email: 'x@example.com', role: 'viewer' },
            bearer,
            h,
        );
        const missingOrg = await invite(
            '11111111-1111-4111-8111-111111111111',
            { email: 'x@example.com', role: 'viewer' },
            bearer,
            h,
        );
        assert.equal(nonMember.status, 404);
        assert.deepEqual(nonMember.body, missingOrg.body);
        assert.equal(h.state.invitations.length, 0);
    } finally {
        await h.close();
    }
});

test('authorization is resolved from an ACTIVE membership of the verified caller, not from the token claim alone', async () => {
    const { h, orgId } = await withOrg(null);
    try {
        const removed = h.addCredential('users');
        h.addMembership(orgId, removed.account_id as string, 'admin', new Date());
        const unverified = h.addCredential('invitedUsers', { email_verified_at: null });
        h.addMembership(orgId, unverified.account_id as string, 'admin');
        const relinked = h.addCredential('users', { account_id: null });
        const noClaim = h.tokenFor(relinked);
        const forgedClaim = h.tokenFor(relinked, { account_id: unverified.account_id });

        const body = { email: 'x@example.com', role: 'viewer' };
        for (const bearer of [h.tokenFor(removed), h.tokenFor(unverified), noClaim, forgedClaim]) {
            assert.equal((await invite(orgId, body, bearer, h)).status, 404);
        }
        assert.equal(h.state.invitations.length, 0);
    } finally {
        await h.close();
    }
});

test('the per-org invites bucket returns 429 once exhausted', async () => {
    const { h, orgId, bearer } = await withOrg('owner');
    try {
        for (let i = 0; i < MAX_ORG_INVITES_PER_WINDOW; i++) {
            const res = await invite(
                orgId,
                { email: `u${i}@example.com`, role: 'viewer' },
                bearer,
                h,
            );
            assert.equal(res.status, 201, `invite ${i}`);
        }
        const over = await invite(orgId, { email: 'over@example.com', role: 'viewer' }, bearer, h);
        assert.equal(over.status, 429);
        assert.equal(h.state.invitations.length, MAX_ORG_INVITES_PER_WINDOW);
    } finally {
        await h.close();
    }
});

test('the per-target-email cap still applies to org invitations', async () => {
    const { h, orgId, bearer } = await withOrg('owner');
    try {
        for (let i = 0; i < MAX_INVITES_PER_WINDOW; i++) {
            const res = await invite(
                orgId,
                { email: 'same@example.com', role: 'viewer' },
                bearer,
                h,
            );
            assert.equal(res.status, 201);
        }
        const over = await invite(orgId, { email: 'SAME@example.com', role: 'viewer' }, bearer, h);
        assert.equal(over.status, 429);
    } finally {
        await h.close();
    }
});

test('with Redis down the invite fails closed (503): nothing is created and no email is queued', async () => {
    const { h, orgId, bearer } = await withOrg('owner');
    try {
        h.state.redisDown = true;
        const res = await invite(orgId, { email: 'x@example.com', role: 'viewer' }, bearer, h);

        assert.equal(res.status, 503);
        assert.equal(h.state.invitations.length, 0);
        assert.equal(h.state.emailJobs.length, 0);
    } finally {
        await h.close();
    }
});

test('a rejected caller cannot spend the organization invite budget', async () => {
    const { h, orgId } = await withOrg('owner');
    try {
        const outsider = h.addCredential('users');
        await invite(orgId, { email: 'x@example.com', role: 'viewer' }, h.tokenFor(outsider), h);
        assert.equal(h.state.redisCounts.size, 0);
    } finally {
        await h.close();
    }
});

test('list shows only pending invitations and never exposes a token or its hash', async () => {
    const { h, orgId, bearer } = await withOrg('admin');
    try {
        await invite(orgId, { email: 'a@example.com', role: 'viewer' }, bearer, h);
        await invite(orgId, { email: 'b@example.com', role: 'viewer' }, bearer, h);
        h.state.invitations[1]!.revoked_at = new Date();
        const otherOrg = h.addOrg('Other');
        h.state.invitations.push({
            id: 'foreign',
            organization_id: otherOrg,
            email: 'c@example.com',
            role: 'viewer',
            invited_by_account_id: 'x',
            token_hash: 'f'.repeat(64),
            expires_at: new Date(Date.now() + 1000),
            accepted_at: null,
            revoked_at: null,
            created_at: new Date(),
        });

        const res = await h.call('GET', `/organizations/${orgId}/invitations`, { bearer });

        assert.equal(res.status, 200);
        const rows = res.body as unknown as { email: string }[];
        assert.deepEqual(
            rows.map((r) => r.email),
            ['a@example.com'],
        );
        assert.ok(!res.text.includes('token'));
        assert.ok(!res.text.includes(h.state.invitations[0]!.token_hash as string));
    } finally {
        await h.close();
    }
});

test("an admin cannot revoke another organization's invitation by id; revoking their own works and kills the token", async () => {
    const { h, orgId, bearer } = await withOrg('admin');
    try {
        const otherOrg = h.addOrg('Other');
        const foreign = {
            id: '22222222-2222-4222-8222-222222222222',
            organization_id: otherOrg,
            email: 'c@example.com',
            role: 'viewer',
            invited_by_account_id: 'x',
            token_hash: 'f'.repeat(64),
            expires_at: new Date(Date.now() + 1000),
            accepted_at: null,
            revoked_at: null,
            created_at: new Date(),
        };
        h.state.invitations.push(foreign);

        const cross = await h.call('DELETE', `/organizations/${orgId}/invitations/${foreign.id}`, {
            bearer,
        });
        assert.equal(cross.status, 404);
        assert.equal(foreign.revoked_at, null);

        await invite(orgId, { email: 'a@example.com', role: 'viewer' }, bearer, h);
        const own = h.state.invitations.find((i) => i.email === 'a@example.com')!;
        const res = await h.call('DELETE', `/organizations/${orgId}/invitations/${own.id}`, {
            bearer,
        });
        assert.equal(res.status, 204);
        assert.ok(own.revoked_at);
        assert.equal(h.state.audit.at(-1)!.action, 'invitation.revoked');
    } finally {
        await h.close();
    }
});

test('path ids must be UUIDs', async () => {
    const { h, bearer } = await withOrg('admin');
    try {
        const res = await h.call('GET', '/organizations/not-a-uuid/invitations', { bearer });
        assert.equal(res.status, 422);
    } finally {
        await h.close();
    }
});

test('requests without the gateway internal token are rejected before any handler runs', async () => {
    const { h, orgId, bearer } = await withOrg('admin');
    try {
        const res = await h.call('GET', `/organizations/${orgId}/invitations`, {
            bearer,
            internal: false,
        });
        assert.ok([401, 403].includes(res.status), String(res.status));
    } finally {
        await h.close();
    }
});

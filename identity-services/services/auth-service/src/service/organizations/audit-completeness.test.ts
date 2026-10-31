import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash, randomBytes, randomUUID } from 'node:crypto';

const { createHarness } = await import('@/test-support/org-invitation-harness');
const { createOrganization } = await import('./organization.service');
const { addMember } = await import('./membership.service');
const { personalOrgId } = await import('@/utils/org-id');

type Harness = Awaited<ReturnType<typeof createHarness>>;

const actions = (h: Harness) => h.state.audit.map((row) => row.action);
const mutationLogs = (h: Harness) => h.state.logs.filter((l) => l.fields.event === 'org_mutation');
const signalLogs = (h: Harness) =>
    h.state.logs.filter((l) => l.fields.event === 'org_owner_role_change_attempt');

const orgWithOwner = async () => {
    const h = await createHarness();
    const owner = h.addCredential('users');
    h.addAccount(owner);
    const orgId = h.addOrg('Acme', owner.account_id as string);
    h.addMembership(orgId, owner.account_id as string, 'owner');
    const member = (role: string) => {
        const credential = h.addCredential('invitedUsers');
        h.addAccount(credential);
        const row = h.addMembership(orgId, credential.account_id as string, role);
        return { credential, row };
    };
    return { h, owner, orgId, member };
};

const assertLogsMatchAudit = (h: Harness) => {
    const logged = mutationLogs(h).map((l) => l.fields.action);
    assert.deepEqual(logged, actions(h));
};

test('create team org: one organization.created row with the creator as actor and target', async () => {
    const h = await createHarness();
    try {
        const creator = h.addCredential('users');
        h.addAccount(creator);

        const res = await h.call('POST', '/organizations', {
            bearer: h.tokenFor(creator),
            body: { name: 'Team' },
        });

        assert.equal(res.status, 201);
        assert.equal(h.state.audit.length, 1);
        const row = h.state.audit[0]!;
        assert.equal(row.action, 'organization.created');
        assert.equal(row.actor_account_id, creator.account_id);
        assert.equal(row.target_account_id, creator.account_id);
        assert.equal(row.old_role, null);
        assert.equal(row.new_role, 'owner');
        assert.equal(h.state.audit[0]!.organization_id, res.body?.id);
        assertLogsMatchAudit(h);
    } finally {
        await h.close();
    }
});

test('create personal org: one organization.created row with no human actor', async () => {
    const h = await createHarness();
    try {
        const owner = h.addCredential('users');

        await createOrganization({
            ownerAccountId: owner.account_id as string,
            name: 'Home',
            id: personalOrgId(owner.id),
        });

        assert.equal(h.state.audit.length, 1);
        assert.equal(h.state.audit[0]!.action, 'organization.created');
        assert.equal(h.state.audit[0]!.actor_account_id, null);
        assertLogsMatchAudit(h);
    } finally {
        await h.close();
    }
});

test('add, then remove, then revive: one row each, and the log lines match the rows', async () => {
    const { h, owner, orgId } = await orgWithOwner();
    try {
        const accountId = randomUUID();
        await addMember({
            organizationId: orgId,
            accountId,
            role: 'viewer',
            actorAccountId: owner.account_id as string,
        });
        const removed = await h.call('DELETE', `/organizations/${orgId}/members/${accountId}`, {
            bearer: h.tokenFor(owner),
        });
        await addMember({
            organizationId: orgId,
            accountId,
            role: 'developer',
            actorAccountId: owner.account_id as string,
        });

        assert.equal(removed.status, 204);
        assert.deepEqual(actions(h), [
            'membership.added',
            'membership.removed',
            'membership.revived',
        ]);
        assert.equal(h.state.audit[2]!.new_role, 'developer');
        assertLogsMatchAudit(h);
    } finally {
        await h.close();
    }
});

test('change role: one row with old and new role, flagged as a role change in the log', async () => {
    const { h, owner, orgId, member } = await orgWithOwner();
    try {
        const target = member('viewer');

        const res = await h.call(
            'PATCH',
            `/organizations/${orgId}/members/${target.credential.account_id}`,
            { bearer: h.tokenFor(owner), body: { role: 'admin' } },
        );

        assert.equal(res.status, 200);
        assert.equal(h.state.audit.length, 1);
        assert.equal(h.state.audit[0]!.old_role, 'viewer');
        assert.equal(h.state.audit[0]!.new_role, 'admin');
        assert.equal(mutationLogs(h)[0]!.fields.role_change, true);
        assertLogsMatchAudit(h);
    } finally {
        await h.close();
    }
});

test('remove and leave: one membership.removed row each, leave has actor equal to target', async () => {
    const { h, owner, orgId, member } = await orgWithOwner();
    try {
        const removedMember = member('developer');
        const leaver = member('viewer');

        await h.call(
            'DELETE',
            `/organizations/${orgId}/members/${removedMember.credential.account_id}`,
            {
                bearer: h.tokenFor(owner),
            },
        );
        await h.call('DELETE', `/organizations/${orgId}/members/${leaver.credential.account_id}`, {
            bearer: h.tokenFor(leaver.credential),
        });

        assert.deepEqual(actions(h), ['membership.removed', 'membership.removed']);
        assert.equal(h.state.audit[0]!.actor_account_id, owner.account_id);
        assert.equal(h.state.audit[1]!.actor_account_id, leaver.credential.account_id);
        assert.equal(h.state.audit[1]!.target_account_id, leaver.credential.account_id);
        assertLogsMatchAudit(h);
    } finally {
        await h.close();
    }
});

test('removal also revokes pending invitations to the removed member, one invitation.revoked row each', async () => {
    const { h, owner, orgId, member } = await orgWithOwner();
    try {
        const target = member('developer');
        for (const role of ['admin', 'viewer']) {
            h.state.invitations.push({
                id: randomUUID(),
                organization_id: orgId,
                email: target.credential.email,
                role,
                invited_by_account_id: owner.account_id,
                token_hash: 'h'.repeat(64),
                expires_at: new Date(Date.now() + 60_000),
                accepted_at: null,
                revoked_at: null,
                created_at: new Date(),
            });
        }

        await h.call('DELETE', `/organizations/${orgId}/members/${target.credential.account_id}`, {
            bearer: h.tokenFor(owner),
        });

        assert.deepEqual(actions(h), [
            'membership.removed',
            'invitation.revoked',
            'invitation.revoked',
        ]);
        assert.ok(h.state.invitations.every((i) => i.revoked_at !== null));
        assert.deepEqual(
            h.state.audit.slice(1).map((a) => a.old_role),
            ['admin', 'viewer'],
        );
        assertLogsMatchAudit(h);
    } finally {
        await h.close();
    }
});

test('invitation create, re-invite and revoke: every state change has its own row', async () => {
    const { h, owner, orgId } = await orgWithOwner();
    try {
        const invite = (role: string) =>
            h.call('POST', `/organizations/${orgId}/invitations`, {
                bearer: h.tokenFor(owner),
                body: { email: 'new@example.com', role },
            });

        await invite('developer');
        await invite('admin');
        const live = h.state.invitations.find((i) => i.revoked_at === null)!;
        const revoked = await h.call('DELETE', `/organizations/${orgId}/invitations/${live.id}`, {
            bearer: h.tokenFor(owner),
        });

        assert.equal(revoked.status, 204);
        assert.deepEqual(actions(h), [
            'invitation.issued',
            'invitation.revoked',
            'invitation.issued',
            'invitation.revoked',
        ]);
        assertLogsMatchAudit(h);
    } finally {
        await h.close();
    }
});

test('accept: membership.added and invitation.accepted, one row per mutated entity, both actor and target are the invitee', async () => {
    const { h, owner, orgId } = await orgWithOwner();
    try {
        const token = randomBytes(32).toString('base64url');
        const invitee = h.addCredential('invitedUsers');
        h.state.invitations.push({
            id: randomUUID(),
            organization_id: orgId,
            email: invitee.email,
            role: 'developer',
            invited_by_account_id: owner.account_id,
            token_hash: createHash('sha256').update(token).digest('hex'),
            expires_at: new Date(Date.now() + 60_000),
            accepted_at: null,
            revoked_at: null,
            created_at: new Date(),
            save: async () => undefined,
        });

        const res = await h.call('POST', '/invitations/accept', {
            bearer: h.tokenFor(invitee),
            body: { token },
        });

        assert.equal(res.status, 200);
        assert.deepEqual(actions(h), ['membership.added', 'invitation.accepted']);
        for (const row of h.state.audit) {
            assert.equal(row.actor_account_id, invitee.account_id);
            assert.equal(row.target_account_id, invitee.account_id);
        }
        assertLogsMatchAudit(h);
    } finally {
        await h.close();
    }
});

test('transfer ownership: a demoted row and a promoted row, both flagged as role changes', async () => {
    const { h, owner, orgId, member } = await orgWithOwner();
    try {
        const target = member('developer');

        const res = await h.call('POST', `/organizations/${orgId}/transfer-ownership`, {
            bearer: h.tokenFor(owner, { auth_time: Math.floor(Date.now() / 1000) }),
            body: { new_owner_account_id: target.credential.account_id },
        });

        assert.equal(res.status, 200);
        assert.deepEqual(actions(h), [
            'membership.ownership_demoted',
            'membership.ownership_promoted',
        ]);
        assert.ok(mutationLogs(h).every((l) => l.fields.role_change === true));
        assertLogsMatchAudit(h);
    } finally {
        await h.close();
    }
});

test('rename: one organization.renamed row', async () => {
    const { h, owner, orgId } = await orgWithOwner();
    try {
        const res = await h.call('PATCH', `/organizations/${orgId}`, {
            bearer: h.tokenFor(owner),
            body: { name: 'Renamed' },
        });

        assert.equal(res.status, 200);
        assert.deepEqual(actions(h), ['organization.renamed']);
        assertLogsMatchAudit(h);
    } finally {
        await h.close();
    }
});

test('a rejected mutation writes no audit row and logs no mutation', async () => {
    const { h, owner, orgId, member } = await orgWithOwner();
    try {
        const developer = member('developer');
        const target = member('viewer');

        const forbidden = await h.call(
            'PATCH',
            `/organizations/${orgId}/members/${target.credential.account_id}`,
            { bearer: h.tokenFor(developer.credential), body: { role: 'admin' } },
        );
        const ownerRow = await h.call(
            'DELETE',
            `/organizations/${orgId}/members/${owner.account_id}`,
            {
                bearer: h.tokenFor(owner),
            },
        );

        assert.equal(forbidden.status, 403);
        assert.equal(ownerRow.status, 409);
        assert.equal(h.state.audit.length, 0);
        assert.equal(mutationLogs(h).length, 0);
    } finally {
        await h.close();
    }
});

test('a transaction that rolls back after its audit row leaves neither a row nor a log line', async () => {
    const { h, owner, orgId, member } = await orgWithOwner();
    try {
        const target = member('viewer');
        h.state.outboxFailOnCall = 1;

        const res = await h.call(
            'PATCH',
            `/organizations/${orgId}/members/${target.credential.account_id}`,
            { bearer: h.tokenFor(owner), body: { role: 'admin' } },
        );

        assert.equal(res.status, 500);
        assert.equal(h.state.audit.length, 0);
        assert.equal(mutationLogs(h).length, 0);
    } finally {
        await h.close();
    }
});

test('mutation log lines carry ids and roles only: no email, token or name', async () => {
    const { h, owner, orgId } = await orgWithOwner();
    try {
        await h.call('POST', `/organizations/${orgId}/invitations`, {
            bearer: h.tokenFor(owner),
            body: { email: 'private.person@example.com', role: 'viewer' },
        });
        const token = h.state.emailJobs[0]!.data.token as string;

        const dump = JSON.stringify(h.state.logs);

        for (const secret of ['private.person', '@example.com', token, 'tester']) {
            assert.ok(!dump.includes(secret), secret);
        }
    } finally {
        await h.close();
    }
});

test('owner-role alert signal: owner role change 409, owner target 409, owner invite 409, and a non-owner transfer are each reported once', async () => {
    const { h, owner, orgId, member } = await orgWithOwner();
    try {
        const admin = member('admin');
        const bearer = h.tokenFor(admin.credential);
        const ownerId = owner.account_id;

        await h.call('PATCH', `/organizations/${orgId}/members/${admin.credential.account_id}`, {
            bearer,
            body: { role: 'owner' },
        });
        await h.call('PATCH', `/organizations/${orgId}/members/${ownerId}`, {
            bearer,
            body: { role: 'viewer' },
        });
        await h.call('POST', `/organizations/${orgId}/invitations`, {
            bearer,
            body: { email: 'x@example.com', role: 'owner' },
        });
        await h.call('POST', `/organizations/${orgId}/transfer-ownership`, {
            bearer: h.tokenFor(admin.credential, { auth_time: Math.floor(Date.now() / 1000) }),
            body: { new_owner_account_id: admin.credential.account_id },
        });

        assert.deepEqual(
            signalLogs(h).map((l) => l.fields.reason),
            [
                'owner_role_requested',
                'target_is_owner',
                'owner_role_requested',
                'transfer_by_non_owner',
            ],
        );
        assert.ok(signalLogs(h).every((l) => l.level === 'warn'));
        assert.ok(
            signalLogs(h).every((l) => l.fields.actor_account_id === admin.credential.account_id),
        );
        assert.equal(h.state.audit.length, 0);
    } finally {
        await h.close();
    }
});

test('owner-role alert signal: a non-member, a developer and a bad session sending role=owner raise nothing, and nothing is written', async () => {
    const { h, orgId, owner, member } = await orgWithOwner();
    try {
        const stranger = h.addCredential('users');
        h.addAccount(stranger);
        const developer = member('developer');
        const sends = (bearer?: string, org = orgId) => [
            h.call('PATCH', `/organizations/${org}/members/${owner.account_id}`, {
                bearer,
                body: { role: 'owner' },
            }),
            h.call('POST', `/organizations/${org}/invitations`, {
                bearer,
                body: { email: 'x@example.com', role: 'owner' },
            }),
        ];

        const responses = await Promise.all([
            ...sends(h.tokenFor(stranger), randomUUID()),
            ...sends(h.tokenFor(stranger)),
            ...sends(h.tokenFor(developer.credential)),
            ...sends('not-a-jwt'),
            ...sends(),
        ]);

        assert.ok(responses.every((r) => [401, 403, 404].includes(r.status)));
        assert.equal(signalLogs(h).length, 0);
        assert.equal(h.state.audit.length, 0);
    } finally {
        await h.close();
    }
});

test('owner-role alert signal: a manager sending role=owner gets 409 and exactly one warn line, with no write', async () => {
    const { h, orgId, owner, member } = await orgWithOwner();
    try {
        const admin = member('admin');
        const target = member('viewer');

        const change = await h.call(
            'PATCH',
            `/organizations/${orgId}/members/${target.credential.account_id}`,
            { bearer: h.tokenFor(admin.credential), body: { role: 'owner' } },
        );
        const invite = await h.call('POST', `/organizations/${orgId}/invitations`, {
            bearer: h.tokenFor(owner),
            body: { email: 'x@example.com', role: 'owner' },
        });

        assert.equal(change.status, 409);
        assert.equal(invite.status, 409);
        assert.deepEqual(
            signalLogs(h).map((l) => [l.level, l.fields.reason, l.fields.actor_account_id]),
            [
                ['warn', 'owner_role_requested', admin.credential.account_id],
                ['warn', 'owner_role_requested', owner.account_id],
            ],
        );
        assert.equal(target.row.role, 'viewer');
        assert.equal(h.state.audit.length, 0);
        assert.equal(h.state.invitations.length, 0);
    } finally {
        await h.close();
    }
});

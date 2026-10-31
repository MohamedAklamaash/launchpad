import { test } from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';

const { createHarness } = await import('@/test-support/org-invitation-harness');
const { MAX_AUDIT_READS_PER_WINDOW } = await import('@/utils/otp-attempts');

type Harness = Awaited<ReturnType<typeof createHarness>>;
type Row = Record<string, unknown>;

const ROLES = ['owner', 'admin', 'developer', 'viewer'] as const;
const BASE_TIME = Date.UTC(2026, 0, 1, 12, 0, 0);

const seedEvent = (h: Harness, orgId: string, offsetSeconds: number, overrides: Row = {}) => {
    const row = {
        id: randomUUID(),
        organization_id: orgId,
        actor_account_id: null,
        action: 'membership.role_changed',
        target_account_id: null,
        old_role: 'viewer',
        new_role: 'developer',
        occurred_at: new Date(BASE_TIME + offsetSeconds * 1000),
        ...overrides,
    };
    h.state.audit.push(row);
    return row;
};

const setup = async (callerRole: string | null = 'admin') => {
    const h = await createHarness();
    const orgId = h.addOrg('Acme', undefined);
    const caller = h.addCredential('users');
    h.addAccount(caller);
    if (callerRole) h.addMembership(orgId, caller.account_id as string, callerRole);
    const get = (query = '', bearer = h.tokenFor(caller), org = orgId) =>
        h.call('GET', `/organizations/${org}/audit-events${query}`, { bearer });
    return { h, orgId, caller, get };
};

const eventIds = (body: Row | null) => (body?.events as Row[]).map((e) => e.id);

test('owner and admin can read the log; developer and viewer get 403 and nothing is read', async () => {
    for (const role of ROLES) {
        const { h, orgId, get } = await setup(role);
        try {
            seedEvent(h, orgId, 0);

            const res = await get();

            if (role === 'owner' || role === 'admin') {
                assert.equal(res.status, 200, role);
                assert.equal((res.body?.events as Row[]).length, 1);
            } else {
                assert.equal(res.status, 403, role);
                assert.ok(!res.text.includes('membership.role_changed'));
            }
        } finally {
            await h.close();
        }
    }
});

test('a non-member and a missing org get the same 404', async () => {
    const { h, orgId, get } = await setup(null);
    try {
        seedEvent(h, orgId, 0);

        const nonMember = await get();
        const missing = await get('', undefined, randomUUID());

        assert.equal(nonMember.status, 404);
        assert.equal(missing.status, 404);
        assert.deepEqual(nonMember.body, missing.body);
    } finally {
        await h.close();
    }
});

test('a removed member and a caller without a verified credential get 404', async () => {
    const { h, orgId, caller, get } = await setup(null);
    try {
        h.addMembership(orgId, caller.account_id as string, 'admin', new Date());
        assert.equal((await get()).status, 404);

        const unverified = h.addCredential('invitedUsers', { email_verified_at: null });
        h.addMembership(orgId, unverified.account_id as string, 'admin');
        assert.equal((await get('', h.tokenFor(unverified))).status, 404);
    } finally {
        await h.close();
    }
});

test('cross-org: an admin of A reads nothing of B, and filters cannot widen the scope', async () => {
    const { h, orgId, get } = await setup('admin');
    try {
        const other = h.addOrg('Other');
        const foreignAccount = randomUUID();
        seedEvent(h, orgId, 0, { target_account_id: foreignAccount });
        seedEvent(h, other, 1, { target_account_id: foreignAccount });

        const direct = await get('', undefined, other);
        const filtered = await get(`?target_account_id=${foreignAccount}`);

        assert.equal(direct.status, 404);
        assert.ok(!direct.text.includes('membership.role_changed'));
        assert.equal((filtered.body?.events as Row[]).length, 1);
    } finally {
        await h.close();
    }
});

test('newest first, cursor pages cover every event once, ties on time are ordered by id', async () => {
    const { h, orgId, get } = await setup('owner');
    try {
        const rows = [
            seedEvent(h, orgId, 10),
            seedEvent(h, orgId, 20),
            seedEvent(h, orgId, 20),
            seedEvent(h, orgId, 20),
            seedEvent(h, orgId, 30),
        ];
        const expected = [...rows]
            .sort(
                (a, b) =>
                    (b.occurred_at as Date).getTime() - (a.occurred_at as Date).getTime() ||
                    String(b.id).localeCompare(String(a.id)),
            )
            .map((r) => r.id);

        const seen: unknown[] = [];
        let cursor: string | null = null;
        let pages = 0;
        do {
            const res = await get(`?limit=2${cursor ? `&cursor=${cursor}` : ''}`);
            assert.equal(res.status, 200);
            seen.push(...eventIds(res.body));
            cursor = res.body?.next_cursor as string | null;
            pages += 1;
        } while (cursor);

        assert.deepEqual(seen, expected);
        assert.equal(pages, 3);
    } finally {
        await h.close();
    }
});

test('an exact multiple of the limit ends with a null cursor, not an empty extra page', async () => {
    const { h, orgId, get } = await setup('owner');
    try {
        seedEvent(h, orgId, 1);
        seedEvent(h, orgId, 2);

        const res = await get('?limit=2');

        assert.equal((res.body?.events as Row[]).length, 2);
        assert.equal(res.body?.next_cursor, null);
    } finally {
        await h.close();
    }
});

test('filters: actor, target, event type and time range, alone and combined', async () => {
    const { h, orgId, get } = await setup('owner');
    try {
        const alice = randomUUID();
        const bob = randomUUID();
        const a = seedEvent(h, orgId, 0, { actor_account_id: alice, target_account_id: bob });
        const b = seedEvent(h, orgId, 60, {
            actor_account_id: bob,
            target_account_id: alice,
            action: 'membership.removed',
        });
        const c = seedEvent(h, orgId, 120, {
            actor_account_id: alice,
            target_account_id: bob,
            action: 'membership.removed',
        });
        const at = (s: number) => new Date(BASE_TIME + s * 1000).toISOString();

        const ids = async (query: string) => eventIds((await get(query)).body);

        assert.deepEqual(await ids(`?actor_account_id=${alice.toUpperCase()}`), [c.id, a.id]);
        assert.deepEqual(await ids(`?target_account_id=${alice}`), [b.id]);
        assert.deepEqual(await ids('?action=membership.removed'), [c.id, b.id]);
        assert.deepEqual(await ids(`?from=${at(30)}`), [c.id, b.id]);
        assert.deepEqual(await ids(`?to=${at(60)}`), [b.id, a.id]);
        assert.deepEqual(await ids(`?from=${at(60)}&to=${at(60)}`), [b.id]);
        assert.deepEqual(await ids(`?actor_account_id=${alice}&action=membership.removed`), [c.id]);
    } finally {
        await h.close();
    }
});

test('invalid query values are 422 before anything is read', async () => {
    const { h, orgId, get } = await setup('owner');
    try {
        seedEvent(h, orgId, 0);
        const bad = [
            '?limit=0',
            '?limit=101',
            '?limit=abc',
            '?limit=1.5',
            '?limit=1&limit=2',
            '?cursor=!!!',
            `?cursor=${Buffer.from('1.not-a-uuid').toString('base64url')}`,
            `?cursor=${'a'.repeat(101)}`,
            '?actor_account_id=not-a-uuid',
            '?target_account_id=1',
            '?action=membership.exploded',
            '?from=yesterday',
            '?to=2026-01-01',
            '?from=2026-02-01T00:00:00Z&to=2026-01-01T00:00:00Z',
            '?unknown=1',
        ];
        for (const query of bad) {
            const res = await get(query);
            assert.equal(res.status, 422, query);
        }
        assert.equal(h.state.redisCounts.size, 0);
    } finally {
        await h.close();
    }
});

test('a forged cursor moves only within the caller org', async () => {
    const { h, orgId, get } = await setup('owner');
    try {
        const other = h.addOrg('Other');
        seedEvent(h, other, 0);
        const mine = seedEvent(h, orgId, 0);
        const forged = Buffer.from(`${BASE_TIME + 999_000}.${randomUUID()}`).toString('base64url');

        const res = await get(`?cursor=${forged}`);

        assert.equal(res.status, 200);
        assert.deepEqual(eventIds(res.body), [mine.id]);
    } finally {
        await h.close();
    }
});

test('response shape: people carry sanitized names and manager-visible emails, invitation events carry no target', async () => {
    const { h, orgId, caller, get } = await setup('admin');
    try {
        const victim = h.addCredential('users', {
            user_name: 'Eve <script>alert(1)</script>@x.io',
        });
        h.addAccount(victim);
        h.addMembership(orgId, victim.account_id as string, 'developer');
        seedEvent(h, orgId, 0, {
            actor_account_id: caller.account_id,
            target_account_id: victim.account_id,
        });
        seedEvent(h, orgId, 1, {
            actor_account_id: caller.account_id,
            action: 'invitation.issued',
            old_role: null,
            new_role: 'viewer',
        });
        const gone = randomUUID();
        seedEvent(h, orgId, 2, { actor_account_id: gone, action: 'membership.removed' });

        const res = await get();

        const [removed, issued, changed] = res.body?.events as Row[];
        assert.deepEqual(Object.keys(changed!).sort(), [
            'action',
            'actor',
            'id',
            'new_role',
            'occurred_at',
            'old_role',
            'target',
        ]);
        const target = changed!.target as Row;
        assert.equal(target.email, victim.email);
        assert.ok(!/[<>@:/.]/.test(target.display_name as string), target.display_name as string);
        assert.equal(issued!.target, null);
        assert.deepEqual(removed!.actor, { account_id: gone, display_name: 'Member' });
    } finally {
        await h.close();
    }
});

test('a former member appears by id and sanitized name only, never with an email', async () => {
    const { h, orgId, caller, get } = await setup('admin');
    try {
        const former = h.addCredential('invitedUsers', { user_name: 'Former Person' });
        h.addAccount(former);
        h.addMembership(orgId, former.account_id as string, 'developer', new Date());
        const current = h.addCredential('invitedUsers', { user_name: 'Current Person' });
        h.addAccount(current);
        h.addMembership(orgId, current.account_id as string, 'viewer');
        seedEvent(h, orgId, 0, {
            actor_account_id: caller.account_id,
            target_account_id: former.account_id,
        });
        seedEvent(h, orgId, 1, {
            actor_account_id: caller.account_id,
            target_account_id: current.account_id,
        });

        const res = await get();

        const [currentEvent, formerEvent] = res.body?.events as Row[];
        assert.deepEqual(formerEvent!.target, {
            account_id: former.account_id,
            display_name: 'Former Person',
        });
        assert.equal((currentEvent!.target as Row).email, current.email);
        assert.equal((currentEvent!.actor as Row).email, caller.email);
        assert.ok(!res.text.includes(former.email));
    } finally {
        await h.close();
    }
});

test('no secrets: an issued and accepted invitation never puts a token, hash or invited email in the log or its response', async () => {
    const { h, orgId, get } = await setup('admin');
    try {
        const inviteRes = await h.call('POST', `/organizations/${orgId}/invitations`, {
            bearer: h.tokenFor(h.state.users[0] as never),
            body: { email: 'secret.invitee@example.com', role: 'developer' },
        });
        assert.equal(inviteRes.status, 201);
        const token = h.state.emailJobs[0]!.data.token as string;
        const hash = h.state.invitations[0]!.token_hash as string;

        const res = await get();

        assert.equal(res.status, 200);
        assert.equal((res.body?.events as Row[]).length, 1);
        for (const secret of [token, hash, 'secret.invitee', 'token', 'hash']) {
            assert.ok(!res.text.includes(secret), secret);
        }
    } finally {
        await h.close();
    }
});

test('rate limit: the 61st read in a minute is 429, and an unreachable limiter is a 503', async () => {
    const { h, get } = await setup('owner');
    try {
        for (let i = 0; i < MAX_AUDIT_READS_PER_WINDOW; i += 1) {
            assert.equal((await get()).status, 200);
        }
        assert.equal((await get()).status, 429);

        h.state.redisDown = true;
        assert.equal((await get()).status, 503);
    } finally {
        await h.close();
    }
});

test('the internal token is required, like every other route', async () => {
    const { h, orgId, caller } = await setup('owner');
    try {
        const res = await h.call('GET', `/organizations/${orgId}/audit-events`, {
            bearer: h.tokenFor(caller),
            internal: false,
        });
        assert.equal(res.status, 401);
    } finally {
        await h.close();
    }
});

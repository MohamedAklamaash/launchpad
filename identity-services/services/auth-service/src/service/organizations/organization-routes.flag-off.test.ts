import { test } from 'node:test';
import assert from 'node:assert/strict';

process.env.ORGS_ENABLED = 'false';
const { createHarness } = await import('@/test-support/org-invitation-harness');

test('flag off: every organization route is a plain 404, even without credentials, and nothing is touched', async () => {
    const h = await createHarness();
    try {
        const org = '11111111-1111-4111-8111-111111111111';
        const probes: [string, string, unknown?][] = [
            [
                'POST',
                `/organizations/${org}/invitations`,
                { email: 'x@example.com', role: 'viewer' },
            ],
            ['GET', `/organizations/${org}/invitations`],
            ['DELETE', `/organizations/${org}/invitations/${org}`],
            ['POST', '/invitations/accept', { token: 'a'.repeat(43) }],
            ['POST', `/organizations/${org}/invitations`, {}],
            ['GET', '/organizations'],
            ['POST', '/organizations', { name: 'Team' }],
            ['PATCH', `/organizations/${org}`, { name: 'Team' }],
            ['GET', `/organizations/${org}/members`],
            ['PATCH', `/organizations/${org}/members/${org}`, { role: 'viewer' }],
            ['DELETE', `/organizations/${org}/members/${org}`],
            ['POST', `/organizations/${org}/transfer-ownership`, { new_owner_account_id: org }],
        ];
        for (const [method, path, body] of probes) {
            const res = await h.call(method, path, { body });
            assert.equal(res.status, 404, `${method} ${path}`);
            assert.deepEqual(res.body, { message: 'Not Found' });
        }
        assert.equal(h.state.redisCounts.size, 0);
        assert.equal(h.state.emailJobs.length, 0);
    } finally {
        await h.close();
    }
});

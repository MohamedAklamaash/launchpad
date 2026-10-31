import { test } from 'node:test';
import assert from 'node:assert/strict';

process.env.ORGS_ENABLED = 'false';
const { createHarness } = await import('@/test-support/org-invitation-harness');

test('flag off: transfer-ownership is a plain 404 for anonymous and authenticated callers, and nothing is touched', async () => {
    const h = await createHarness();
    try {
        const org = '11111111-1111-4111-8111-111111111111';
        const owner = h.addCredential('users');
        const bearer = h.tokenFor(owner, { auth_time: Math.floor(Date.now() / 1000) });
        const path = `/organizations/${org}/transfer-ownership`;
        const body = { new_owner_account_id: org };

        for (const res of [
            await h.call('POST', path, { body }),
            await h.call('POST', path, { body, bearer }),
            await h.call('POST', path, { body: {} }),
        ]) {
            assert.equal(res.status, 404);
            assert.deepEqual(res.body, { message: 'Not Found' });
        }
        assert.equal(h.state.published.length, 0);
        assert.equal(h.state.audit.length, 0);
        assert.equal(h.state.queryLog.length, 0);
    } finally {
        await h.close();
    }
});

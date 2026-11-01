import { test } from 'node:test';
import assert from 'node:assert/strict';

process.env.NODE_ENV = 'test';
process.env.INTERNAL_API_TOKEN = 'x'.repeat(32);
process.env.DATABASE_USER_NAME = 'test';
process.env.DATABASE_PASSWORD = 'test';
process.env.DATABASE_HOST = '127.0.0.1';
process.env.DATABASE_PORT = '1';
process.env.DATABASE_NAME = 'test';
process.env.AUTH_DB_URL = 'postgres://test:test@127.0.0.1:1/test';
process.env.JWT_SECRET = 'x'.repeat(32);
process.env.JWT_REFRESH_SECRET = 'y'.repeat(32);
process.env.JWT_EXPIRES_IN = '15m';
process.env.JWT_REFRESH_EXPIRES_IN = '7d';
process.env.GATEWAY_URL = 'http://localhost:8000';
process.env.GITHUB_TOKEN = 'ghp_test';
process.env.GITHUB_CLIENT_ID = 'test';
process.env.GITHUB_CLIENT_SECRET = 'test';
process.env.REDIS_HOST = '127.0.0.1';
process.env.REDIS_PORT = '1';
process.env.REDIS_PASSWORD = 'test';
process.env.RABBITMQ_URL = 'amqp://guest:guest@127.0.0.1:1/';
process.env.ORGS_ENABLED = 'false';
// Pinned off explicitly — this file proves the flag's whole point:
// "No request behaviour change" (task scope) must hold even if something calls this
// service layer before the flag is flipped.

const { createOrganization } = await import('@/service/organizations/organization.service');
const { addMember } = await import('@/service/organizations/membership.service');
const { issueInvitation } = await import('@/service/organizations/invitation.service');
const { HttpError } = await import('@launchpad/common');

test('every mutating org/membership/invitation function rejects outright while ORGS_ENABLED is off', async () => {
    await assert.rejects(
        () => createOrganization({ ownerAccountId: 'account-1', name: 'Acme' }),
        HttpError,
    );
    await assert.rejects(
        () =>
            addMember({
                organizationId: 'org-1',
                accountId: 'account-2',
                role: 'admin',
                actorAccountId: 'account-1',
            }),
        HttpError,
    );
    await assert.rejects(
        () =>
            issueInvitation({
                organizationId: 'org-1',
                email: 'invitee@example.com',
                role: 'admin',
                invitedByAccountId: 'account-1',
            }),
        HttpError,
    );
});

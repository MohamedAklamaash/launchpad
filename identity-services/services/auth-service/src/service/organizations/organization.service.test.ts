import { test, mock } from 'node:test';
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
process.env.ORGS_ENABLED = 'true';

const { sequelize } = await import('@/db/sequalize');
const { Organization, Membership, MembershipAuditEvent } = await import('@/db');
const { createOrganization } = await import('@/service/organizations/organization.service');
const { orgEventsPublisher } = await import('@/messaging/producer/org-events.message');
const { stubOutbox } = await import('@/test-support/outbox-stub');
await stubOutbox();

const withTransactionMock = () =>
    mock.method(sequelize, 'transaction', async (cb: (t: object) => unknown) => cb({}));

test('createOrganization writes the Organization row and its owner Membership row atomically, then publishes org.created', async () => {
    const txSpy = withTransactionMock();
    const orgCreateSpy = mock.method(
        Organization,
        'create',
        async (attrs: { id?: string; owner_account_id: string; name: string }) => ({
            id: attrs.id ?? 'org-generated-id',
            owner_account_id: attrs.owner_account_id,
            name: attrs.name,
        }),
    );
    const membershipCreateSpy = mock.method(
        Membership,
        'create',
        async (attrs: { organization_id: string; account_id: string; role: string }) => ({
            ...attrs,
            id: 'membership-1',
            version: 1,
        }),
    );
    const auditSpy = mock.method(MembershipAuditEvent, 'create', async (attrs: object) => attrs);
    // Mocked to a no-op so the assertion below is on the routing key this call publishes
    // with, not on the real buffer-and-log-warn behavior a never-connected
    // ResilientAmqpPublisher already has (amqp-publisher.ts) when nothing mocks it away.
    const publishSpy = mock.method(orgEventsPublisher, 'publish', () => {});

    try {
        const { organization, membership } = await createOrganization({
            ownerAccountId: 'account-1',
            name: 'Acme',
        });

        assert.equal(organization.owner_account_id, 'account-1');
        assert.equal(membership.account_id, 'account-1');
        assert.equal(membership.role, 'owner');
        assert.equal(membership.version, 1);
        assert.equal(orgCreateSpy.mock.callCount(), 1);
        assert.equal(membershipCreateSpy.mock.callCount(), 1);
        assert.equal(auditSpy.mock.callCount(), 1);
        assert.equal(publishSpy.mock.callCount(), 1);

        const [routingKey] = publishSpy.mock.calls[0]!.arguments;
        assert.equal(routingKey, 'org.created');
    } finally {
        txSpy.mock.restore();
        orgCreateSpy.mock.restore();
        membershipCreateSpy.mock.restore();
        auditSpy.mock.restore();
        publishSpy.mock.restore();
    }
});

test('createOrganization passes a deterministic id straight through to the Organization row (personal-org backfill path)', async () => {
    const txSpy = withTransactionMock();
    const orgCreateSpy = mock.method(
        Organization,
        'create',
        async (attrs: { id?: string; owner_account_id: string; name: string }) => ({
            id: attrs.id,
            owner_account_id: attrs.owner_account_id,
            name: attrs.name,
        }),
    );
    const membershipCreateSpy = mock.method(Membership, 'create', async () => ({
        id: 'membership-1',
        organization_id: 'org-fixed-id',
        account_id: 'account-1',
        role: 'owner',
        version: 1,
    }));
    const auditSpy = mock.method(MembershipAuditEvent, 'create', async (attrs: object) => attrs);
    const publishSpy = mock.method(orgEventsPublisher, 'publish', () => {});

    try {
        const { organization } = await createOrganization({
            id: 'org-fixed-id',
            ownerAccountId: 'account-1',
            name: 'Personal',
        });
        assert.equal(organization.id, 'org-fixed-id');
    } finally {
        txSpy.mock.restore();
        orgCreateSpy.mock.restore();
        membershipCreateSpy.mock.restore();
        auditSpy.mock.restore();
        publishSpy.mock.restore();
    }
});

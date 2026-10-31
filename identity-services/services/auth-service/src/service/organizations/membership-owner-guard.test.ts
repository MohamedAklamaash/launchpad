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

// This repo's auth-service has no migration framework and this test suite must not touch
// a real Postgres, so "inserting a second owner row fails" is verified two ways (see
// src/db/membership-owner-index.test.ts for the first):
//   2. HERE — the service-level guard: no function in membership.service.ts/
//      organization.service.ts other than createOrganization can ever write a Membership
//      row with role 'owner', so application code has no path to even attempt the insert
//      the DB's partial unique index would reject.
const { sequelize } = await import('@/db/sequalize');
const { Membership } = await import('@/db');
const { addMember, changeMemberRole, removeMember } =
    await import('@/service/organizations/membership.service');
const { HttpError } = await import('@launchpad/common');

const withLockingTransactionMock = () =>
    mock.method(sequelize, 'transaction', async (cb: (t: object) => unknown) =>
        cb({ LOCK: { UPDATE: 'UPDATE' } }),
    );

test('addMember refuses to create a second owner row, never reaching the database', async () => {
    const createSpy = mock.method(Membership, 'create', async () => {
        throw new Error('Membership.create must not be called for role=owner');
    });
    try {
        await assert.rejects(
            () =>
                addMember({
                    organizationId: 'org-1',
                    accountId: 'account-2',
                    role: 'owner',
                    actorAccountId: 'account-1',
                }),
            HttpError,
        );
        assert.equal(createSpy.mock.callCount(), 0);
    } finally {
        createSpy.mock.restore();
    }
});

test('changeMemberRole refuses to promote any member to owner, never reaching the database', async () => {
    const findSpy = mock.method(Membership, 'findOne', async () => {
        throw new Error('Membership.findOne must not be called for newRole=owner');
    });
    try {
        await assert.rejects(
            () =>
                changeMemberRole({
                    organizationId: 'org-1',
                    accountId: 'account-2',
                    newRole: 'owner',
                    actorAccountId: 'account-1',
                }),
            HttpError,
        );
        assert.equal(findSpy.mock.callCount(), 0);
    } finally {
        findSpy.mock.restore();
    }
});

test('changeMemberRole refuses to change the role of an existing owner row (no demotion outside ownership transfer)', async () => {
    const txSpy = withLockingTransactionMock();
    const findSpy = mock.method(Membership, 'findOne', async () => ({
        id: 'membership-1',
        organization_id: 'org-1',
        account_id: 'account-1',
        role: 'owner',
        version: 1,
    }));
    try {
        await assert.rejects(
            () =>
                changeMemberRole({
                    organizationId: 'org-1',
                    accountId: 'account-1',
                    newRole: 'admin',
                    actorAccountId: 'account-1',
                }),
            HttpError,
        );
    } finally {
        txSpy.mock.restore();
        findSpy.mock.restore();
    }
});

test('removeMember refuses to remove the organization owner, leaving the row untouched', async () => {
    const txSpy = withLockingTransactionMock();
    const destroySpy = mock.fn(async () => {
        throw new Error('destroy must not be called on an owner row');
    });
    const findSpy = mock.method(Membership, 'findOne', async () => ({
        id: 'membership-1',
        organization_id: 'org-1',
        account_id: 'account-1',
        role: 'owner',
        version: 1,
        destroy: destroySpy,
    }));
    try {
        await assert.rejects(
            () =>
                removeMember({
                    organizationId: 'org-1',
                    accountId: 'account-1',
                    actorAccountId: null,
                }),
            HttpError,
        );
        assert.equal(destroySpy.mock.callCount(), 0);
    } finally {
        txSpy.mock.restore();
        findSpy.mock.restore();
    }
});

import { test, mock } from 'node:test';
import assert from 'node:assert/strict';
import { Op } from 'sequelize';

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
const { Invitation, InvitedUser, Membership, MembershipAuditEvent, RefreshToken, User } =
    await import('@/db');

mock.method(Invitation, 'update', async () => [0]);
mock.method(Invitation, 'findAll', async () => []);
const { addMember, changeMemberRole, removeMember } =
    await import('@/service/organizations/membership.service');
const { orgEventsPublisher } = await import('@/messaging/producer/org-events.message');
const { stubOutbox } = await import('@/test-support/outbox-stub');
await stubOutbox();

// removeMember now also revokes the removed account's refresh tokens (security review
// H3(b)) — every removeMember test needs these mocked so it never falls through to a
// real (nonexistent) Postgres connection.
const stubNoLinkedCredentials = () => {
    const userFindAllSpy = mock.method(User, 'findAll', async () => []);
    const invitedFindAllSpy = mock.method(InvitedUser, 'findAll', async () => []);
    const refreshDestroySpy = mock.method(RefreshToken, 'destroy', async () => 0);
    return { userFindAllSpy, invitedFindAllSpy, refreshDestroySpy };
};

const withLockingTransactionMock = () =>
    mock.method(sequelize, 'transaction', async (cb: (t: object) => unknown) =>
        cb({ LOCK: { UPDATE: 'UPDATE' }, afterCommit: () => undefined }),
    );

test('addMember creates a non-owner Membership, records one audit event, and publishes org.membership.changed', async () => {
    const txSpy = withLockingTransactionMock();
    const findSpy = mock.method(Membership, 'findOne', async () => null);
    const createSpy = mock.method(Membership, 'create', async (attrs: object) => ({
        ...attrs,
        id: 'membership-2',
        version: 1,
    }));
    const auditSpy = mock.method(MembershipAuditEvent, 'create', async (attrs: object) => attrs);
    const publishSpy = mock.method(orgEventsPublisher, 'publish', () => {});

    try {
        const membership = await addMember({
            organizationId: 'org-1',
            accountId: 'account-2',
            role: 'developer',
            actorAccountId: 'account-1',
        });

        assert.equal(membership.role, 'developer');
        assert.equal(auditSpy.mock.callCount(), 1);
        assert.equal(publishSpy.mock.callCount(), 1);
        assert.equal(publishSpy.mock.calls[0]!.arguments[0], 'org.membership.changed');
    } finally {
        txSpy.mock.restore();
        findSpy.mock.restore();
        createSpy.mock.restore();
        auditSpy.mock.restore();
        publishSpy.mock.restore();
    }
});

test('changeMemberRole bumps the version, writes old_role/new_role to the audit event, and publishes with the new version', async () => {
    const txSpy = withLockingTransactionMock();
    const findSpy = mock.method(Membership, 'findOne', async () => ({
        id: 'membership-2',
        organization_id: 'org-1',
        account_id: 'account-2',
        role: 'developer',
        version: 1,
    }));
    // changeMemberRole delegates the actual increment to bumpMembershipVersion, which is a
    // raw UPDATE ... RETURNING — mocking sequelize.query directly (rather than the
    // imported function binding, which ESM makes read-only) exercises the real call path.
    const querySpy = mock.method(sequelize, 'query', async () => [{ version: 2 }]);
    const auditSpy = mock.method(MembershipAuditEvent, 'create', async (attrs: object) => attrs);
    const publishSpy = mock.method(orgEventsPublisher, 'publish', () => {});

    try {
        const membership = await changeMemberRole({
            organizationId: 'org-1',
            accountId: 'account-2',
            newRole: 'admin',
            actorAccountId: 'account-1',
        });

        assert.equal(membership.role, 'admin');
        assert.equal(membership.version, 2);
        assert.equal(auditSpy.mock.callCount(), 1);
        const auditCall = auditSpy.mock.calls[0]!.arguments[0] as {
            old_role: string;
            new_role: string;
        };
        assert.equal(auditCall.old_role, 'developer');
        assert.equal(auditCall.new_role, 'admin');
        assert.equal(publishSpy.mock.callCount(), 1);
    } finally {
        txSpy.mock.restore();
        findSpy.mock.restore();
        querySpy.mock.restore();
        auditSpy.mock.restore();
        publishSpy.mock.restore();
    }
});

test('removeMember tombstones (never deletes) a non-owner Membership, bumps its version, and publishes org.membership.removed with the bumped version', async () => {
    const txSpy = withLockingTransactionMock();
    const destroySpy = mock.fn(async () => {});
    const findSpy = mock.method(Membership, 'findOne', async () => ({
        id: 'membership-2',
        organization_id: 'org-1',
        account_id: 'account-2',
        role: 'developer',
        version: 3,
        destroy: destroySpy,
    }));
    // removeMember delegates the tombstone + increment to bumpMembershipVersionForRemoval,
    // a raw UPDATE ... RETURNING, same reasoning as changeMemberRole's test above.
    const querySpy = mock.method(sequelize, 'query', async () => [{ version: 4 }]);
    const auditSpy = mock.method(MembershipAuditEvent, 'create', async (attrs: object) => attrs);
    const publishSpy = mock.method(orgEventsPublisher, 'publish', () => {});
    const linkedInvitedUserId = 'invited-user-1';
    const { userFindAllSpy, invitedFindAllSpy, refreshDestroySpy } = stubNoLinkedCredentials();
    invitedFindAllSpy.mock.restore();
    const realInvitedFindAllSpy = mock.method(InvitedUser, 'findAll', async () => [
        { id: linkedInvitedUserId, email: 'Linked@Example.com' },
    ]);

    try {
        await removeMember({
            organizationId: 'org-1',
            accountId: 'account-2',
            actorAccountId: 'account-1',
        });

        // The row is tombstoned (removed_at set via the raw UPDATE), never destroy()ed —
        // a hard delete would leave nothing for a stale redelivered event to be rejected
        // against, which is exactly the resurrection race this fix closes.
        assert.equal(destroySpy.mock.callCount(), 0);
        assert.equal(auditSpy.mock.callCount(), 1);
        assert.equal(publishSpy.mock.callCount(), 1);
        assert.equal(publishSpy.mock.calls[0]!.arguments[0], 'org.membership.removed');
        // publish() receives the JSON-serialized envelope as a Buffer, not the plain
        // payload object — parse it back out to assert on the bumped version.
        const publishedBody = publishSpy.mock.calls[0]!.arguments[1] as Buffer;
        const publishedEvent = JSON.parse(publishedBody.toString()) as {
            payload: { version: number };
        };
        assert.equal(publishedEvent.payload.version, 4);

        // Security review H3(b): the removed account's refresh tokens are revoked,
        // mapped from account_id through to the credential row(s) it's linked to.
        assert.equal(refreshDestroySpy.mock.callCount(), 1);
        const [{ where }] = refreshDestroySpy.mock.calls[0]!.arguments as [
            { where: { user_id: { [Op.in]: string[] } } },
        ];
        assert.deepEqual(where.user_id[Op.in], [linkedInvitedUserId]);
    } finally {
        txSpy.mock.restore();
        findSpy.mock.restore();
        querySpy.mock.restore();
        auditSpy.mock.restore();
        publishSpy.mock.restore();
        userFindAllSpy.mock.restore();
        realInvitedFindAllSpy.mock.restore();
        refreshDestroySpy.mock.restore();
    }
});

// Security review H3(b): the removed account could be a GitHub OAuth owner (`User`),
// not just a password account — the mapping must check both credential tables.
test('removeMember revokes refresh tokens through the User table too, and is a no-op when nothing is linked', async () => {
    const txSpy = withLockingTransactionMock();
    const findSpy = mock.method(Membership, 'findOne', async () => ({
        id: 'membership-3',
        organization_id: 'org-1',
        account_id: 'account-3',
        role: 'developer',
        version: 1,
        destroy: async () => {},
    }));
    const querySpy = mock.method(sequelize, 'query', async () => [{ version: 2 }]);
    const auditSpy = mock.method(MembershipAuditEvent, 'create', async (attrs: object) => attrs);
    const publishSpy = mock.method(orgEventsPublisher, 'publish', () => {});
    const linkedUserId = 'github-user-1';
    const userFindAllSpy = mock.method(User, 'findAll', async () => [
        { id: linkedUserId, email: 'gh@example.com' },
    ]);
    const invitedFindAllSpy = mock.method(InvitedUser, 'findAll', async () => []);
    const refreshDestroySpy = mock.method(RefreshToken, 'destroy', async () => 1);

    try {
        await removeMember({
            organizationId: 'org-1',
            accountId: 'account-3',
            actorAccountId: 'account-1',
        });

        assert.equal(refreshDestroySpy.mock.callCount(), 1);
        const [{ where }] = refreshDestroySpy.mock.calls[0]!.arguments as [
            { where: { user_id: { [Op.in]: string[] } } },
        ];
        assert.deepEqual(where.user_id[Op.in], [linkedUserId]);
    } finally {
        txSpy.mock.restore();
        findSpy.mock.restore();
        querySpy.mock.restore();
        auditSpy.mock.restore();
        publishSpy.mock.restore();
        userFindAllSpy.mock.restore();
        invitedFindAllSpy.mock.restore();
        refreshDestroySpy.mock.restore();
    }
});

test('removeMember never calls RefreshToken.destroy when the account is linked to no credential row', async () => {
    const txSpy = withLockingTransactionMock();
    const findSpy = mock.method(Membership, 'findOne', async () => ({
        id: 'membership-4',
        organization_id: 'org-1',
        account_id: 'account-4',
        role: 'developer',
        version: 1,
        destroy: async () => {},
    }));
    const querySpy = mock.method(sequelize, 'query', async () => [{ version: 2 }]);
    const auditSpy = mock.method(MembershipAuditEvent, 'create', async (attrs: object) => attrs);
    const publishSpy = mock.method(orgEventsPublisher, 'publish', () => {});
    const { userFindAllSpy, invitedFindAllSpy, refreshDestroySpy } = stubNoLinkedCredentials();

    try {
        await removeMember({
            organizationId: 'org-1',
            accountId: 'account-4',
            actorAccountId: 'account-1',
        });

        assert.equal(refreshDestroySpy.mock.callCount(), 0);
    } finally {
        txSpy.mock.restore();
        findSpy.mock.restore();
        querySpy.mock.restore();
        auditSpy.mock.restore();
        publishSpy.mock.restore();
        userFindAllSpy.mock.restore();
        invitedFindAllSpy.mock.restore();
        refreshDestroySpy.mock.restore();
    }
});

test('addMember revives a tombstoned Membership with a bumped version instead of creating a second row', async () => {
    const txSpy = withLockingTransactionMock();
    const createSpy = mock.method(Membership, 'create', async () => {
        throw new Error('Membership.create must not be called when a tombstoned row exists');
    });
    const findSpy = mock.method(Membership, 'findOne', async () => ({
        id: 'membership-2',
        organization_id: 'org-1',
        account_id: 'account-2',
        role: 'viewer',
        version: 4,
        removed_at: new Date('2026-01-01T00:00:00Z'),
    }));
    const querySpy = mock.method(sequelize, 'query', async () => [{ version: 5 }]);
    const auditSpy = mock.method(MembershipAuditEvent, 'create', async (attrs: object) => attrs);
    const publishSpy = mock.method(orgEventsPublisher, 'publish', () => {});

    try {
        const membership = await addMember({
            organizationId: 'org-1',
            accountId: 'account-2',
            role: 'developer',
            actorAccountId: 'account-1',
        });

        assert.equal(createSpy.mock.callCount(), 0);
        assert.equal(membership.role, 'developer');
        assert.equal(membership.version, 5);
        assert.equal(membership.removed_at, null);
        const auditCall = auditSpy.mock.calls[0]!.arguments[0] as { action: string };
        assert.equal(auditCall.action, 'membership.revived');
        assert.equal(publishSpy.mock.callCount(), 1);
        assert.equal(publishSpy.mock.calls[0]!.arguments[0], 'org.membership.changed');
    } finally {
        txSpy.mock.restore();
        createSpy.mock.restore();
        findSpy.mock.restore();
        querySpy.mock.restore();
        auditSpy.mock.restore();
        publishSpy.mock.restore();
    }
});

test('addMember refuses to add an account that is already an active (non-tombstoned) member', async () => {
    const txSpy = withLockingTransactionMock();
    const createSpy = mock.method(Membership, 'create', async () => {
        throw new Error('Membership.create must not be called for an already-active member');
    });
    const findSpy = mock.method(Membership, 'findOne', async () => ({
        id: 'membership-2',
        organization_id: 'org-1',
        account_id: 'account-2',
        role: 'viewer',
        version: 1,
        removed_at: null,
    }));
    try {
        const { HttpError } = await import('@launchpad/common');
        await assert.rejects(
            () =>
                addMember({
                    organizationId: 'org-1',
                    accountId: 'account-2',
                    role: 'developer',
                    actorAccountId: 'account-1',
                }),
            HttpError,
        );
        assert.equal(createSpy.mock.callCount(), 0);
    } finally {
        txSpy.mock.restore();
        findSpy.mock.restore();
    }
});

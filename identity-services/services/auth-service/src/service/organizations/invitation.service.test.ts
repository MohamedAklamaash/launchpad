import { test, mock } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';

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
const { Invitation, MembershipAuditEvent } = await import('@/db');
const { issueInvitation, verifyInvitationToken, revokeInvitation } =
    await import('@/service/organizations/invitation.service');
const { HttpError } = await import('@launchpad/common');

mock.method(Invitation, 'update', async () => [0]);
mock.method(Invitation, 'findAll', async () => []);

interface FakeInvitationRow {
    id: string;
    organization_id: string;
    email: string;
    role: string;
    invited_by_account_id: string;
    token_hash: string;
    expires_at: Date;
    accepted_at: Date | null;
    revoked_at: Date | null;
    save?: () => Promise<void>;
}

const makeFakeInvitations = () => {
    const rows: FakeInvitationRow[] = [];
    return {
        rows,
        create: async (attrs: Omit<FakeInvitationRow, 'id' | 'accepted_at' | 'revoked_at'>) => {
            const row: FakeInvitationRow = {
                ...attrs,
                id: `invitation-${rows.length + 1}`,
                accepted_at: null,
                revoked_at: null,
            };
            rows.push(row);
            return row;
        },
        findOne: async (opts: { where: { token_hash?: string; id?: string } }) => {
            const row = rows.find(
                (r) =>
                    (opts.where.token_hash === undefined ||
                        r.token_hash === opts.where.token_hash) &&
                    (opts.where.id === undefined || r.id === opts.where.id),
            );
            if (!row) return null;
            return Object.assign(row, { save: async () => {} });
        },
    };
};

const withLockingTransactionMock = () =>
    mock.method(sequelize, 'transaction', async (cb: (t: object) => unknown) =>
        cb({ LOCK: { UPDATE: 'UPDATE' }, afterCommit: () => undefined }),
    );

test('issueInvitation mints a random token, stores only its SHA-256 hash, and never derives the token from any id', async () => {
    const fake = makeFakeInvitations();
    const txSpy = withLockingTransactionMock();
    const createSpy = mock.method(Invitation, 'create', fake.create);
    const auditSpy = mock.method(MembershipAuditEvent, 'create', async (attrs: object) => attrs);

    try {
        const { invitation, token } = await issueInvitation({
            organizationId: 'org-1',
            email: 'invitee@example.com',
            role: 'admin',
            invitedByAccountId: 'account-1',
        });

        assert.equal(invitation.token_hash, createHash('sha256').update(token).digest('hex'));
        assert.equal(invitation.token_hash.length, 64);
        assert.match(invitation.token_hash, /^[0-9a-f]{64}$/);

        // Never derived from any id this call carries.
        assert.notEqual(token, 'org-1');
        assert.notEqual(token, 'account-1');
        assert.notEqual(token, invitation.id);
        assert.ok(!invitation.token_hash.includes('org-1'));
        assert.ok(!invitation.token_hash.includes('account-1'));
        assert.equal(auditSpy.mock.callCount(), 1);

        const second = await issueInvitation({
            organizationId: 'org-1',
            email: 'invitee@example.com',
            role: 'admin',
            invitedByAccountId: 'account-1',
        });
        // Same org/email/role/inviter twice in a row still produces two unrelated
        // tokens — proves the token is random, not a deterministic function of its
        // inputs (which is exactly what an account id is, and exactly what a bearer
        // credential must never be).
        assert.notEqual(token, second.token);
        assert.notEqual(invitation.token_hash, second.invitation.token_hash);
    } finally {
        txSpy.mock.restore();
        createSpy.mock.restore();
        auditSpy.mock.restore();
    }
});

test('issueInvitation refuses to invite anyone as owner, before any transaction opens', async () => {
    const fake = makeFakeInvitations();
    const createSpy = mock.method(Invitation, 'create', fake.create);
    try {
        await assert.rejects(
            () =>
                issueInvitation({
                    organizationId: 'org-1',
                    email: 'invitee@example.com',
                    role: 'owner',
                    invitedByAccountId: 'account-1',
                }),
            HttpError,
        );
        assert.equal(fake.rows.length, 0);
    } finally {
        createSpy.mock.restore();
    }
});

test('verifyInvitationToken only looks up by the hash, never accepts the plaintext as a key, and rejects used/expired/revoked tokens', async () => {
    const fake = makeFakeInvitations();
    const txSpy = withLockingTransactionMock();
    const createSpy = mock.method(Invitation, 'create', fake.create);
    const auditSpy = mock.method(MembershipAuditEvent, 'create', async (attrs: object) => attrs);
    const findSpy = mock.method(Invitation, 'findOne', fake.findOne);

    try {
        const { token } = await issueInvitation({
            organizationId: 'org-1',
            email: 'invitee@example.com',
            role: 'viewer',
            invitedByAccountId: 'account-1',
        });

        const resolved = await verifyInvitationToken(token);
        assert.ok(resolved);
        assert.equal(resolved?.organization_id, 'org-1');

        assert.equal(await verifyInvitationToken('not-the-real-token'), null);

        fake.rows[0].revoked_at = new Date();
        assert.equal(await verifyInvitationToken(token), null);

        fake.rows[0].revoked_at = null;
        fake.rows[0].accepted_at = new Date();
        assert.equal(await verifyInvitationToken(token), null);

        fake.rows[0].accepted_at = null;
        fake.rows[0].expires_at = new Date(Date.now() - 1000);
        assert.equal(await verifyInvitationToken(token), null);
    } finally {
        txSpy.mock.restore();
        createSpy.mock.restore();
        auditSpy.mock.restore();
        findSpy.mock.restore();
    }
});

test('revokeInvitation marks the row revoked and records one audit event in the same transaction', async () => {
    const fake = makeFakeInvitations();
    fake.rows.push({
        id: 'invitation-1',
        organization_id: 'org-1',
        email: 'invitee@example.com',
        role: 'admin',
        invited_by_account_id: 'account-1',
        token_hash: 'a'.repeat(64),
        expires_at: new Date(Date.now() + 1000),
        accepted_at: null,
        revoked_at: null,
    });

    const txSpy = withLockingTransactionMock();
    const findSpy = mock.method(Invitation, 'findOne', fake.findOne);
    const auditSpy = mock.method(MembershipAuditEvent, 'create', async (attrs: object) => attrs);

    try {
        await revokeInvitation('invitation-1', 'org-1', 'account-1');

        assert.ok(fake.rows[0].revoked_at);
        assert.equal(auditSpy.mock.callCount(), 1);
        const auditCall = auditSpy.mock.calls[0]!.arguments[0] as {
            action: string;
            target_account_id: string | null;
            old_role: string | null;
        };
        assert.equal(auditCall.action, 'invitation.revoked');
        assert.equal(auditCall.target_account_id, null);
        assert.equal(auditCall.old_role, 'admin');
    } finally {
        txSpy.mock.restore();
        findSpy.mock.restore();
        auditSpy.mock.restore();
    }
});

test('revokeInvitation 404s on an unknown or already-used invitation, writing no audit event', async () => {
    const txSpy = withLockingTransactionMock();
    const findSpy = mock.method(Invitation, 'findOne', async () => null);
    const auditSpy = mock.method(MembershipAuditEvent, 'create', async (attrs: object) => attrs);

    try {
        await assert.rejects(() => revokeInvitation('missing', 'org-1', 'account-1'), HttpError);
        assert.equal(auditSpy.mock.callCount(), 0);
    } finally {
        txSpy.mock.restore();
        findSpy.mock.restore();
        auditSpy.mock.restore();
    }
});

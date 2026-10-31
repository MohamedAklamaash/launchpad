import { test, mock } from 'node:test';
import assert from 'node:assert/strict';

process.env.NODE_ENV = 'test';
process.env.INTERNAL_API_TOKEN = 'x'.repeat(32);
process.env.DATABASE_USER_NAME = 'test';
process.env.DATABASE_PASSWORD = 'test';
process.env.DATABASE_HOST = 'localhost';
process.env.DATABASE_NAME = 'test';
process.env.AUTH_DB_URL = 'postgres://test:test@localhost:5432/test';
process.env.JWT_SECRET = 'x'.repeat(32);
process.env.JWT_REFRESH_SECRET = 'y'.repeat(32);
process.env.JWT_EXPIRES_IN = '15m';
process.env.JWT_REFRESH_EXPIRES_IN = '7d';
process.env.GATEWAY_URL = 'http://localhost:8000';
process.env.GITHUB_TOKEN = 'ghp_test';
process.env.GITHUB_CLIENT_ID = 'test';
process.env.GITHUB_CLIENT_SECRET = 'test';
process.env.REDIS_HOST = 'localhost';
process.env.REDIS_PASSWORD = 'test';
process.env.RABBITMQ_URL = 'amqp://guest:guest@localhost:5672/';
process.env.ORGS_ENABLED = 'true';

const { sequelize } = await import('@/db/sequalize');
const { Account, InvitedUser, RefreshToken, User } = await import('@/db');
const { UserFacadeService } = await import('@/service/user.facade.service');
const { HttpError } = await import('@launchpad/common');
const { verifyAccessToken } = await import('@/utils/handle-token');
const { accountIdForEmail } = await import('@/utils/account-id');

const withTransactionMock = () =>
    mock.method(sequelize, 'transaction', async (cb: (t: object) => unknown) => cb({}));

const service = new UserFacadeService();

const withAdvisoryLockMock = () => mock.method(sequelize, 'query', async () => []);

const githubData = {
    token: 'gh-token',
    github_id: '12345',
    username: 'octocat',
    avatar_url: 'https://example.com/avatar.png',
    email: 'octocat@example.com',
};

// Security review follow-up ("organic GitHub signups after the backfill have no
// Account-linking hook"): GitHub OAuth is itself the proof of email ownership, so a
// brand-new User row must be linked to its deterministic Account immediately, not
// left to wait on a batch backfill that denies it every role under ticket 7 in the
// meantime.
test('upsertUser links a newly-created GitHub User to its deterministic Account', async () => {
    const txSpy = withTransactionMock();
    const lockSpy = withAdvisoryLockMock();
    const findUserSpy = mock.method(User, 'findOne', async () => null);
    const createdUser = {
        id: '11111111-1111-1111-1111-111111111111',
        email: githubData.email,
        user_name: githubData.username,
        role: 'super_admin',
        infra_id: [] as string[],
        account_id: null as string | null,
        created_at: new Date(),
        metadata: {},
    };
    const createUserSpy = mock.method(User, 'create', async () => createdUser);
    // `findUserSpy` above already covers linkAccountForVerifiedIdentity's own
    // collision-check call to User.findOne (also returns null — no conflict).
    const invitedFindOneSpy = mock.method(InvitedUser, 'findOne', async () => null);
    const findOrCreateSpy = mock.method(Account, 'findOrCreate', async () => [{}, true]);
    const accountUpdateSpy = mock.method(User, 'update', async () => [1]);
    const refreshSpy = mock.method(RefreshToken, 'create', async (attrs: object) => ({
        ...attrs,
        token_id: 'tok-1',
    }));

    try {
        const result = await service.upsertUser(githubData);

        const expectedAccountId = accountIdForEmail(githubData.email!);
        const payload = verifyAccessToken(result.accessToken);
        assert.equal(payload.account_id, expectedAccountId);

        assert.deepEqual(accountUpdateSpy.mock.calls[0]!.arguments[0], {
            account_id: expectedAccountId,
        });
        assert.deepEqual(
            (accountUpdateSpy.mock.calls[0]!.arguments[1] as { where: object }).where,
            {
                id: '11111111-1111-1111-1111-111111111111',
                account_id: null,
            },
        );
    } finally {
        txSpy.mock.restore();
        lockSpy.mock.restore();
        findUserSpy.mock.restore();
        createUserSpy.mock.restore();
        invitedFindOneSpy.mock.restore();
        findOrCreateSpy.mock.restore();
        accountUpdateSpy.mock.restore();
        refreshSpy.mock.restore();
    }
});

test('upsertUser leaves an already-linked GitHub User alone (no re-link attempt)', async () => {
    const txSpy = withTransactionMock();
    const existingUser = {
        id: '11111111-1111-1111-1111-111111111111',
        email: githubData.email,
        user_name: githubData.username,
        role: 'super_admin',
        infra_id: [] as string[],
        account_id: 'already-linked-account-id',
        created_at: new Date(),
        metadata: {},
        profile_url: null as string | null,
        async save() {
            return this;
        },
    };
    const findUserSpy = mock.method(User, 'findOne', async () => existingUser);
    // If the already-linked case ever started calling the link flow by mistake, this
    // would throw — proving it never does.
    const invitedFindOneSpy = mock.method(InvitedUser, 'findOne', async () => {
        throw new Error('must not run the link flow when already linked');
    });
    const refreshSpy = mock.method(RefreshToken, 'create', async (attrs: object) => ({
        ...attrs,
        token_id: 'tok-1',
    }));

    try {
        const result = await service.upsertUser(githubData);
        const payload = verifyAccessToken(result.accessToken);
        assert.equal(payload.account_id, 'already-linked-account-id');
    } finally {
        txSpy.mock.restore();
        findUserSpy.mock.restore();
        invitedFindOneSpy.mock.restore();
        refreshSpy.mock.restore();
    }
});

// Security review R2's collision rule applies here too: never merge into an Account a
// different credential row already holds, even for GitHub's own "verified by
// construction" rows.
test('upsertUser declines to link when the Account is already held by a different row', async () => {
    const txSpy = withTransactionMock();
    const lockSpy = withAdvisoryLockMock();
    const createdUser = {
        id: '11111111-1111-1111-1111-111111111111',
        email: githubData.email,
        user_name: githubData.username,
        role: 'super_admin',
        infra_id: [] as string[],
        account_id: null as string | null,
        created_at: new Date(),
        metadata: {},
    };
    const createUserSpy = mock.method(User, 'create', async () => createdUser);
    // One mock covers both calls this test exercises: upsertUser's own "does a User
    // row already exist for this github id/email" lookups (plain object where clauses
    // without an `account_id` key — both answered `null`, so upsertUser takes the
    // create path) and linkAccountForVerifiedIdentity's collision check (where:
    // {account_id: ...}, answered with a conflicting row).
    const findUserSpy = mock.method(User, 'findOne', async (opts: { where?: object }) =>
        opts?.where && 'account_id' in opts.where ? { id: 'some-other-user-row' } : null,
    );
    const invitedFindOneSpy = mock.method(InvitedUser, 'findOne', async () => null);
    const findOrCreateSpy = mock.method(Account, 'findOrCreate', async () => {
        throw new Error('must not create/touch the Account when declining to link');
    });
    const updateSpy = mock.method(User, 'update', async () => {
        throw new Error('must not link account_id when declining');
    });
    const refreshSpy = mock.method(RefreshToken, 'create', async (attrs: object) => ({
        ...attrs,
        token_id: 'tok-1',
    }));

    try {
        const result = await service.upsertUser(githubData);
        const payload = verifyAccessToken(result.accessToken);
        assert.equal('account_id' in payload, false);
    } finally {
        txSpy.mock.restore();
        lockSpy.mock.restore();
        findUserSpy.mock.restore();
        createUserSpy.mock.restore();
        invitedFindOneSpy.mock.restore();
        findOrCreateSpy.mock.restore();
        updateSpy.mock.restore();
        refreshSpy.mock.restore();
    }
});

const linkedRow = (githubId: string | null) => {
    const row = {
        id: '22222222-2222-2222-2222-222222222222',
        email: githubData.email,
        user_name: githubData.username,
        role: 'super_admin',
        infra_id: [] as string[],
        account_id: 'already-linked-account-id',
        created_at: new Date(),
        metadata: (githubId === null ? {} : { github: { id: githubId } }) as Record<
            string,
            unknown
        >,
        profile_url: null as string | null,
        saves: 0,
        async save() {
            row.saves += 1;
            return row;
        },
    };
    return row;
};

const isEmailLookup = (opts?: { where?: object }) => !!opts?.where && 'email' in opts.where;

const withRefreshTokenMock = () =>
    mock.method(RefreshToken, 'create', async (attrs: object) => ({ ...attrs, token_id: 'tok-1' }));

test('upsertUser: the same github id logs in normally and never consults the email', async () => {
    const txSpy = withTransactionMock();
    const row = linkedRow(githubData.github_id);
    const findSpy = mock.method(User, 'findOne', async (opts?: { where?: object }) =>
        isEmailLookup(opts) ? null : row,
    );
    const refreshSpy = withRefreshTokenMock();

    try {
        const result = await service.upsertUser(githubData);
        assert.equal(verifyAccessToken(result.accessToken).account_id, 'already-linked-account-id');
        assert.equal(findSpy.mock.callCount(), 1);
        assert.equal(row.saves, 1);
    } finally {
        txSpy.mock.restore();
        findSpy.mock.restore();
        refreshSpy.mock.restore();
    }
});

test('upsertUser: a legacy row with no github id is bound by email once and the id is recorded', async () => {
    const row = linkedRow(null);

    const txSpy = withTransactionMock();
    const findSpy = mock.method(User, 'findOne', async (opts?: { where?: object }) =>
        isEmailLookup(opts) ? row : null,
    );
    const refreshSpy = withRefreshTokenMock();
    try {
        await service.upsertUser(githubData);
        assert.equal((row.metadata.github as { id: string }).id, githubData.github_id);
        assert.equal(row.saves, 1);
    } finally {
        txSpy.mock.restore();
        findSpy.mock.restore();
        refreshSpy.mock.restore();
    }

    const txSpy2 = withTransactionMock();
    const findSpy2 = mock.method(User, 'findOne', async (opts?: { where?: object }) =>
        isEmailLookup(opts) ? null : row,
    );
    const refreshSpy2 = withRefreshTokenMock();
    try {
        await service.upsertUser(githubData);
        assert.equal(findSpy2.mock.callCount(), 1);
    } finally {
        txSpy2.mock.restore();
        findSpy2.mock.restore();
        refreshSpy2.mock.restore();
    }
});

test('upsertUser: a different github id with the same email never reaches the original row', async () => {
    const txSpy = withTransactionMock();
    const row = linkedRow('99999');
    const findSpy = mock.method(User, 'findOne', async (opts?: { where?: object }) =>
        isEmailLookup(opts) ? row : null,
    );
    const createSpy = mock.method(User, 'create', async () => {
        throw new Error('must not create a second row for a unique email');
    });
    const refreshSpy = withRefreshTokenMock();

    try {
        await assert.rejects(
            () => service.upsertUser(githubData),
            (err: unknown) => err instanceof HttpError && err.statusCode === 409,
        );
        assert.equal(row.saves, 0);
        assert.deepEqual(row.metadata, { github: { id: '99999' } });
        assert.equal(refreshSpy.mock.callCount(), 0);
        assert.equal(createSpy.mock.callCount(), 0);
    } finally {
        txSpy.mock.restore();
        findSpy.mock.restore();
        createSpy.mock.restore();
        refreshSpy.mock.restore();
    }
});

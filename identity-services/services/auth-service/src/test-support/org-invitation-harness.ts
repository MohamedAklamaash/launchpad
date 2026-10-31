import { mock } from 'node:test';
import { createServer, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { randomUUID } from 'node:crypto';

// Test-only dummy values. Ports are deliberately 1: if any stub below is missed, the
// service fails loudly with ECONNREFUSED instead of reaching a real local Redis, Postgres
// or RabbitMQ.
process.env.NODE_ENV = 'test';
process.env.INTERNAL_API_TOKEN = 'i'.repeat(32);
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
process.env.ORGS_ENABLED ??= 'true';

const Redis = (await import('ioredis')).default;
const { Op, UniqueConstraintError } = await import('sequelize');
const { sequelize } = await import('@/db/sequalize');
const db = await import('@/db');
const { createApp } = await import('@/app');
const { signAccessToken } = await import('@/utils/handle-token');
const { accountIdForEmail } = await import('@/utils/account-id');
const queueModule = await import('@/messaging/producer/user-created.message');
const { orgEventsPublisher } = await import('@/messaging/producer/org-events.message');

export const INTERNAL_TOKEN = process.env.INTERNAL_API_TOKEN;

type Row = Record<string, unknown>;

const matches = (row: Row, where: Row): boolean =>
    Object.entries(where).every(([key, value]) =>
        value !== null && typeof value === 'object' ? true : row[key] === value,
    );

export interface Credential {
    id: string;
    email: string;
    user_name: string;
    account_id: string | null;
    email_verified_at?: Date | null;
}

export const createHarness = async () => {
    const state = {
        users: [] as Row[],
        invitedUsers: [] as Row[],
        orgs: [] as Row[],
        memberships: [] as Row[],
        invitations: [] as Row[],
        accounts: [] as Row[],
        audit: [] as Row[],
        emailJobs: [] as { name: string; data: Row; opts: Row }[],
        published: [] as { routingKey: string; payload: Row }[],
        redisCounts: new Map<string, number>(),
        redisDown: false,
        membershipCreateError: null as Error | null,
    };

    let tail: Promise<unknown> = Promise.resolve();
    const mocks = [
        // Serializes whole transactions: stands in for the row lock Postgres would take on the
        // invitation (FOR UPDATE), since there is no Postgres in unit tests. It does not model
        // rollback; the assertions below only rely on ordering.
        mock.method(sequelize, 'transaction', (cb: (t: object) => Promise<unknown>) => {
            const run = tail.then(() => cb({ LOCK: { UPDATE: 'UPDATE' } }));
            tail = run.catch(() => undefined);
            return run;
        }),
        mock.method(sequelize, 'query', async (sql: string, opts: { replacements?: Row }) => {
            if (sql.includes('pg_advisory_xact_lock')) return [];
            const membership = state.memberships.find(
                (m) => m.id === opts.replacements?.membershipId,
            );
            if (!membership) return [];
            if (sql.includes('removed_at = NOW()')) {
                membership.version = (membership.version as number) + 1;
                membership.removed_at = new Date();
                return [{ version: membership.version }];
            }
            membership.version = (membership.version as number) + 1;
            membership.role = opts.replacements?.role ?? membership.role;
            membership.removed_at = null;
            return [{ version: membership.version }];
        }),
        mock.method(Redis.prototype, 'eval', async (_s: string, _n: number, key: string) => {
            if (state.redisDown) throw new Error('redis unavailable');
            const next = (state.redisCounts.get(key) ?? 0) + 1;
            state.redisCounts.set(key, next);
            return next;
        }),
        mock.method(
            db.User,
            'findByPk',
            async (id: string) => state.users.find((u) => u.id === id) ?? null,
        ),
        mock.method(
            db.InvitedUser,
            'findByPk',
            async (id: string) => state.invitedUsers.find((u) => u.id === id) ?? null,
        ),
        mock.method(
            db.User,
            'findOne',
            async (o: { where: Row }) => state.users.find((u) => matches(u, o.where)) ?? null,
        ),
        mock.method(
            db.InvitedUser,
            'findOne',
            async (o: { where: Row }) =>
                state.invitedUsers.find((u) => matches(u, o.where)) ?? null,
        ),
        mock.method(db.User, 'update', async (values: Row, o: { where: Row }) => {
            state.users.filter((u) => matches(u, o.where)).forEach((u) => Object.assign(u, values));
            return [1];
        }),
        mock.method(db.InvitedUser, 'update', async (values: Row, o: { where: Row }) => {
            state.invitedUsers
                .filter((u) => matches(u, o.where))
                .forEach((u) => Object.assign(u, values));
            return [1];
        }),
        mock.method(db.Account, 'findOrCreate', async (o: { where: Row; defaults: Row }) => {
            if (!state.accounts.some((a) => matches(a, o.where))) state.accounts.push(o.defaults);
            return [o.defaults, true];
        }),
        mock.method(
            db.Organization,
            'findByPk',
            async (id: string) => state.orgs.find((o) => o.id === id) ?? null,
        ),
        mock.method(
            db.Membership,
            'findOne',
            async (o: { where: Row }) => state.memberships.find((m) => matches(m, o.where)) ?? null,
        ),
        mock.method(db.User, 'findAll', async (o: { where: Row }) =>
            state.users.filter((u) => matches(u, o.where)),
        ),
        mock.method(db.InvitedUser, 'findAll', async (o: { where: Row }) =>
            state.invitedUsers.filter((u) => matches(u, o.where)),
        ),
        mock.method(db.RefreshToken, 'destroy', async () => 0),
        mock.method(db.Invitation, 'update', async (values: Row, o: { where: Row }) => {
            const emails = (o.where.email as { [Op.in]?: string[] } | string | undefined) ?? null;
            const wanted = typeof emails === 'object' && emails ? (emails[Op.in] ?? []) : null;
            const hits = state.invitations.filter(
                (i) =>
                    matches(i, o.where) && (wanted === null || wanted.includes(i.email as string)),
            );
            hits.forEach((i) => Object.assign(i, values));
            return [hits.length];
        }),
        mock.method(db.Membership, 'create', async (attrs: Row) => {
            if (state.membershipCreateError) throw state.membershipCreateError;
            const row = { id: randomUUID(), version: 1, removed_at: null, ...attrs };
            state.memberships.push(row);
            return row;
        }),
        mock.method(db.Invitation, 'create', async (attrs: Row) => {
            const row: Row = {
                id: randomUUID(),
                accepted_at: null,
                revoked_at: null,
                created_at: new Date(),
                ...attrs,
                save: async () => undefined,
            };
            state.invitations.push(row);
            return row;
        }),
        mock.method(
            db.Invitation,
            'findOne',
            async (o: { where: Row }) => state.invitations.find((i) => matches(i, o.where)) ?? null,
        ),
        mock.method(db.Invitation, 'findAll', async (o: { where: Row }) =>
            state.invitations.filter(
                (i) => matches(i, o.where) && (i.expires_at as Date).getTime() > Date.now(),
            ),
        ),
        mock.method(db.MembershipAuditEvent, 'create', async (attrs: Row) => {
            state.audit.push(attrs);
            return attrs;
        }),
        mock.method(
            queueModule.userAuthenticationQueue,
            'add',
            async (name: string, data: Row, opts: Row) => {
                state.emailJobs.push({ name, data, opts });
                return undefined as never;
            },
        ),
        mock.method(orgEventsPublisher, 'publish', (routingKey: string, body: Buffer) => {
            state.published.push({
                routingKey,
                payload: (JSON.parse(body.toString()) as { payload: Row }).payload,
            });
        }),
    ];

    const server: Server = createServer(createApp());
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
    const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;

    const addOrg = (name = 'Acme <b>Corp</b>') => {
        const org = { id: randomUUID(), name };
        state.orgs.push(org);
        return org.id;
    };

    const addCredential = (
        table: 'users' | 'invitedUsers',
        overrides: Partial<Credential> = {},
    ): Credential => {
        const email = overrides.email ?? `${randomUUID()}@example.com`;
        const row: Credential = {
            id: randomUUID(),
            email,
            user_name: 'tester',
            account_id: accountIdForEmail(email),
            ...(table === 'invitedUsers' ? { email_verified_at: new Date() } : {}),
            ...overrides,
        };
        state[table].push(row as unknown as Row);
        return row;
    };

    const addMembership = (
        organization_id: string,
        account_id: string,
        role: string,
        removed_at: Date | null = null,
    ) => {
        const row = { id: randomUUID(), organization_id, account_id, role, version: 1, removed_at };
        state.memberships.push(row);
        return row;
    };

    const tokenFor = (credential: Credential, extra: Record<string, unknown> = {}) =>
        signAccessToken({
            sub: credential.id,
            email: credential.email,
            user_name: credential.user_name,
            role: 'user',
            ...(credential.account_id ? { account_id: credential.account_id } : {}),
            ...extra,
        });

    const call = async (
        method: string,
        path: string,
        opts: { bearer?: string; body?: unknown; internal?: boolean } = {},
    ) => {
        const headers: Record<string, string> = { 'content-type': 'application/json' };
        if (opts.internal !== false) headers['x-internal-token'] = INTERNAL_TOKEN;
        if (opts.bearer) headers.authorization = `Bearer ${opts.bearer}`;
        const res = await fetch(`${base}/api/v1${path}`, {
            method,
            headers,
            body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
        });
        const text = await res.text();
        return { status: res.status, body: text ? (JSON.parse(text) as Row) : null, text };
    };

    return {
        state,
        addOrg,
        addCredential,
        addMembership,
        tokenFor,
        call,
        newUniqueError: () => new UniqueConstraintError({ message: 'duplicate' }),
        close: async () => {
            await new Promise<void>((resolve) => server.close(() => resolve()));
            mocks.forEach((m) => m.mock.restore());
        },
    };
};

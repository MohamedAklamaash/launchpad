import { mock } from 'node:test';
import { UniqueConstraintError } from 'sequelize';

// In-memory stand-in for the organizations/memberships tables, mocked at the Sequelize
// model boundary. The caller must have set the test env vars (ports pointing at
// 127.0.0.1:1) before the first import of '@/db', as every other auth-service test does.
export interface StoredOrg {
    id: string;
    owner_account_id: string;
    name: string;
}

export interface StoredMembership {
    id: string;
    organization_id: string;
    account_id: string;
    role: string;
    version: number;
    removed_at: Date | null;
}

export interface PublishedEvent {
    routingKey: string;
    payload: { organization_id: string; owner_account_id: string; version: number };
}

export const installPersonalOrgStore = async () => {
    const { Organization, Membership, MembershipAuditEvent, OrgEventOutbox } = await import('@/db');
    const { orgEventsPublisher, publishOutboxEvent } =
        await import('@/messaging/producer/org-events.message');

    const orgs = new Map<string, StoredOrg>();
    const memberships: StoredMembership[] = [];
    const published: PublishedEvent[] = [];

    mock.method(Organization, 'findByPk', async (id: string) => orgs.get(id) ?? null);
    mock.method(Organization, 'create', async (attrs: StoredOrg) => {
        if (orgs.has(attrs.id)) throw new UniqueConstraintError({});
        orgs.set(attrs.id, { ...attrs });
        return orgs.get(attrs.id);
    });
    mock.method(Membership, 'create', async (attrs: Partial<StoredMembership>) => {
        const row = {
            id: `membership-${memberships.length + 1}`,
            version: 1,
            removed_at: null,
            ...attrs,
        } as StoredMembership;
        memberships.push(row);
        return row;
    });
    mock.method(
        Membership,
        'findOne',
        async (opts: { where: Partial<StoredMembership> }) =>
            memberships.find(
                (m) =>
                    m.organization_id === opts.where.organization_id &&
                    m.account_id === opts.where.account_id &&
                    m.role === opts.where.role &&
                    m.removed_at === opts.where.removed_at,
            ) ?? null,
    );
    mock.method(MembershipAuditEvent, 'create', async (attrs: object) => attrs);
    mock.method(
        OrgEventOutbox,
        'create',
        async (attrs: { routing_key: string; payload: Record<string, unknown> }) => {
            publishOutboxEvent(attrs.routing_key, attrs.payload, new Date());
            return attrs;
        },
    );
    mock.method(orgEventsPublisher, 'publish', (routingKey: string, content: Buffer) => {
        const event = JSON.parse(content.toString()) as PublishedEvent;
        published.push({ routingKey, payload: event.payload });
    });

    return { orgs, memberships, published };
};

import {
    ORG_CREATED_ROUTING_KEY,
    ORG_INVITATION_ACCEPTED_ROUTING_KEY,
    ORG_MEMBERSHIP_CHANGED_ROUTING_KEY,
    ORG_MEMBERSHIP_REMOVED_ROUTING_KEY,
    type OrgCreatedPayload,
    type OrgInvitationAcceptedPayload,
    type OrgMembershipChangedPayload,
    type OrgMembershipRemovedPayload,
} from '@launchpad/common';
import { Op, QueryTypes, type Transaction } from 'sequelize';
import { OrgEventOutbox } from '@/db';
import { sequelize } from '@/db/sequalize';
import { orgEventsPublisher, publishOutboxEvent } from '@/messaging/producer/org-events.message';
import { logger } from '@/utils/logger';

const BATCH_SIZE = 100;
const POLL_INTERVAL_MS = 500;
const MAX_BACKOFF_MS = 30_000;
// Only one dispatcher cluster-wide may publish at a time, or two replicas could interleave
// rows and break per-membership order.
const DISPATCHER_LOCK_KEY = 7_203_001;

// Insert in the SAME transaction as the state change the event describes. If that
// transaction rolls back, so does the event; if the process dies after commit, the row is
// still pending and the dispatcher publishes it on its next run.
const enqueue = (
    transaction: Transaction,
    routingKey: string,
    organizationId: string,
    payload: object,
) =>
    OrgEventOutbox.create(
        {
            organization_id: organizationId,
            routing_key: routingKey,
            payload: payload as Record<string, unknown>,
        },
        { transaction },
    );

export const enqueueOrgCreated = (transaction: Transaction, payload: OrgCreatedPayload) =>
    enqueue(transaction, ORG_CREATED_ROUTING_KEY, payload.organization_id, payload);

export const enqueueMembershipChanged = (
    transaction: Transaction,
    payload: OrgMembershipChangedPayload,
) => enqueue(transaction, ORG_MEMBERSHIP_CHANGED_ROUTING_KEY, payload.organization_id, payload);

export const enqueueMembershipRemoved = (
    transaction: Transaction,
    payload: OrgMembershipRemovedPayload,
) => enqueue(transaction, ORG_MEMBERSHIP_REMOVED_ROUTING_KEY, payload.organization_id, payload);

export const enqueueInvitationAccepted = (
    transaction: Transaction,
    payload: OrgInvitationAcceptedPayload,
) => enqueue(transaction, ORG_INVITATION_ACCEPTED_ROUTING_KEY, payload.organization_id, payload);

export type DrainStatus = 'idle' | 'published' | 'disconnected' | 'busy';

export interface DrainResult {
    status: DrainStatus;
    published: number;
}

const assertBatchConfirmed = async (lossesBefore: number, bufferedBefore: number) => {
    await orgEventsPublisher.waitForConfirms();
    // A channel lost mid-batch hides its in-flight messages from the replacement channel's
    // waitForConfirms, and a publish that raced a disconnect was buffered, not sent.
    if (
        orgEventsPublisher.confirmChannelLossCount() !== lossesBefore ||
        orgEventsPublisher.bufferedWhileDisconnectedCount() !== bufferedBefore
    ) {
        throw new Error('org.events channel was lost during the batch; leaving rows pending');
    }
};

// One dispatcher pass. Rows are marked sent only after the broker confirmed the whole batch,
// inside the same transaction that holds the dispatcher lock, so a failure anywhere leaves
// every row pending and the next pass re-publishes in the same order. Delivery is therefore
// at-least-once; the mirrors drop a repeated or older version.
export const drainOutbox = async (): Promise<DrainResult> => {
    if (!orgEventsPublisher.isConnected()) return { status: 'disconnected', published: 0 };

    return sequelize.transaction(async (transaction) => {
        const [lock] = await sequelize.query<{ locked: boolean }>(
            'SELECT pg_try_advisory_xact_lock(:key) AS locked',
            { replacements: { key: DISPATCHER_LOCK_KEY }, type: QueryTypes.SELECT, transaction },
        );
        if (!lock?.locked) return { status: 'busy' as const, published: 0 };

        const rows = await OrgEventOutbox.findAll({
            where: { sent_at: null },
            order: [['seq', 'ASC']],
            limit: BATCH_SIZE,
            transaction,
        });
        if (rows.length === 0) return { status: 'idle' as const, published: 0 };

        const lossesBefore = orgEventsPublisher.confirmChannelLossCount();
        const bufferedBefore = orgEventsPublisher.bufferedWhileDisconnectedCount();
        for (const row of rows) {
            publishOutboxEvent(row.routing_key, row.payload, row.created_at);
        }
        await assertBatchConfirmed(lossesBefore, bufferedBefore);

        await OrgEventOutbox.update(
            { sent_at: new Date() },
            { where: { seq: { [Op.in]: rows.map((row) => row.seq) } }, transaction },
        );
        return { status: 'published' as const, published: rows.length };
    });
};

export const countPendingOutboxRows = (): Promise<number> =>
    OrgEventOutbox.count({ where: { sent_at: null } });

// For one-shot scripts: keeps draining until nothing is pending or a pass fails. Returns
// the number of rows still pending, so 0 means the broker confirmed everything.
export const drainOutboxFully = async (maxPasses = 200): Promise<number> => {
    for (let pass = 0; pass < maxPasses; pass += 1) {
        try {
            const result = await drainOutbox();
            if (result.status === 'idle') break;
            if (result.status !== 'published') await sleep(POLL_INTERVAL_MS);
        } catch (error) {
            logger.error({ err: error }, 'outbox drain pass failed');
            break;
        }
    }
    return countPendingOutboxRows();
};

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

export const nextDispatchDelay = (failures: number, lastPublished: number): number => {
    if (failures > 0) return Math.min(POLL_INTERVAL_MS * 2 ** failures, MAX_BACKOFF_MS);
    return lastPublished >= BATCH_SIZE ? 0 : POLL_INTERVAL_MS;
};

let timer: NodeJS.Timeout | null = null;
let stopped = true;

const runDispatcherPass = async (failures: number): Promise<void> => {
    let nextFailures = 0;
    let published = 0;
    try {
        published = (await drainOutbox()).published;
    } catch (error) {
        nextFailures = failures + 1;
        logger.error({ err: error, failures: nextFailures }, 'org.events outbox dispatch failed');
    }
    if (stopped) return;
    timer = setTimeout(
        () => void runDispatcherPass(nextFailures),
        nextDispatchDelay(nextFailures, published),
    );
    timer.unref();
};

export const startOutboxDispatcher = (): void => {
    if (!stopped) return;
    stopped = false;
    void runDispatcherPass(0);
};

export const stopOutboxDispatcher = (): void => {
    stopped = true;
    if (timer) clearTimeout(timer);
    timer = null;
};

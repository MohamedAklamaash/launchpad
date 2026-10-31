import {
    ResilientAmqpPublisher,
    ORG_EVENT_EXCHANGE,
    ORG_CREATED_ROUTING_KEY,
    ORG_MEMBERSHIP_CHANGED_ROUTING_KEY,
    ORG_MEMBERSHIP_REMOVED_ROUTING_KEY,
    ORG_INVITATION_ACCEPTED_ROUTING_KEY,
    type OrgCreatedPayload,
    type OrgMembershipChangedPayload,
    type OrgMembershipRemovedPayload,
    type OrgInvitationAcceptedPayload,
} from '@launchpad/common';
import { env } from '@/config/env';
import { logger } from '@/utils/logger';

// Mirrors user-created.message.ts's shape. ORGS_ENABLED gates connection and every publish
// below — no request behavior change for F9 ticket 2: with the flag off (default), this
// publisher never connects and the exchange is never declared on the broker.
export const orgEventsPublisher = new ResilientAmqpPublisher({
    url: env.RABBITMQ_URL,
    exchange: ORG_EVENT_EXCHANGE,
    exchangeType: 'topic',
    maxRetries: env.AMQP_MAX_RETRIES,
    retryDelay: env.AMQP_RETRY_DELAY_MS,
    maxBufferSize: 200,
    name: 'auth-org-events',
});

export const InitOrgEventsPublisher = async () => {
    if (!env.ORGS_ENABLED) {
        return;
    }
    if (!env.RABBITMQ_URL) {
        logger.error('RABBITMQ_URL not set in auth service');
        return;
    }
    await orgEventsPublisher.connect();
    logger.info('Auth service org.events publisher initialized');
};

const publish = <T extends Record<string, unknown>>(routingKey: string, payload: T) => {
    if (!env.ORGS_ENABLED) {
        // Never buffer-and-drop against a publisher that was never connected — see
        // InitOrgEventsPublisher's matching early return.
        return;
    }
    const event = {
        type: routingKey,
        payload,
        occured_at: new Date().toISOString(),
        metadata: { version: 1 },
    };
    orgEventsPublisher.publish(routingKey, Buffer.from(JSON.stringify(event)), {
        contentType: 'application/json',
        persistent: true,
    });
};

export const publishOrgCreated = (payload: OrgCreatedPayload) =>
    publish(ORG_CREATED_ROUTING_KEY, payload);

export const publishMembershipChanged = (payload: OrgMembershipChangedPayload) =>
    publish(ORG_MEMBERSHIP_CHANGED_ROUTING_KEY, payload);

export const publishMembershipRemoved = (payload: OrgMembershipRemovedPayload) =>
    publish(ORG_MEMBERSHIP_REMOVED_ROUTING_KEY, payload);

export const publishInvitationAccepted = (payload: OrgInvitationAcceptedPayload) =>
    publish(ORG_INVITATION_ACCEPTED_ROUTING_KEY, payload);

export interface OrgEventsDeliveryReport {
    delivered: boolean;
    pending: number;
    bufferedWhileDisconnected: number;
    error?: string;
}

// For one-shot scripts: delivered only if the broker confirmed every publish and none was
// ever published while disconnected (a buffered message can be dropped or lost at close).
export const verifyOrgEventsDelivered = async (): Promise<OrgEventsDeliveryReport> => {
    const pending = orgEventsPublisher.pendingCount();
    const bufferedWhileDisconnected = orgEventsPublisher.bufferedWhileDisconnectedCount();
    let error: string | undefined;
    try {
        await orgEventsPublisher.waitForConfirms();
    } catch (err) {
        error = err instanceof Error ? err.message : String(err);
    }
    return {
        delivered: pending === 0 && bufferedWhileDisconnected === 0 && error === undefined,
        pending,
        bufferedWhileDisconnected,
        ...(error === undefined ? {} : { error }),
    };
};

export const closeOrgEventsPublisher = async () => {
    if (!env.ORGS_ENABLED) {
        return;
    }
    await orgEventsPublisher.close();
    logger.info('Auth service org.events publisher closed');
};

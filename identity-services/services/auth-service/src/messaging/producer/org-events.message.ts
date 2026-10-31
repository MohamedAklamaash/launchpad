import { ResilientAmqpPublisher, ORG_EVENT_EXCHANGE } from '@launchpad/common';
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
    // Confirms are mandatory: the outbox marks a row sent only after the broker confirmed it.
    orgEventsPublisher.enableConfirms();
    if (!env.RABBITMQ_URL) {
        logger.error('RABBITMQ_URL not set in auth service');
        return;
    }
    await orgEventsPublisher.connect();
    logger.info('Auth service org.events publisher initialized');
};

// Called only by the outbox dispatcher, on a confirm channel and only while connected.
export const publishOutboxEvent = (
    routingKey: string,
    payload: Record<string, unknown>,
    occuredAt: Date,
) => {
    const event = {
        type: routingKey,
        payload,
        occured_at: occuredAt.toISOString(),
        metadata: { version: 1 },
    };
    orgEventsPublisher.publish(routingKey, Buffer.from(JSON.stringify(event)), {
        contentType: 'application/json',
        persistent: true,
    });
};

export const closeOrgEventsPublisher = async () => {
    if (!env.ORGS_ENABLED) {
        return;
    }
    await orgEventsPublisher.close();
    logger.info('Auth service org.events publisher closed');
};

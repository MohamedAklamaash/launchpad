import {
    ResilientAmqpConsumer,
    INFRA_EVENT_EXCHANGE,
    INFRA_CREATED_ROUTING_KEY,
    type MessageHandler,
} from '@launchpad/common';
import { env } from '@/config/env';
import { logger } from '@/utils/logger';
import { User } from '@/db/models/user.model';

const QUEUE_NAME = 'auth-service.infra-events';

const handler: MessageHandler = async (msg, channel) => {
    const correlationId =
        (msg.properties.correlationId as string | undefined) ?? crypto.randomUUID();

    // Step 1: Parse — discard unparseable messages immediately
    let content: { payload?: { user_id?: string; infra_id?: string } };
    try {
        content = JSON.parse(msg.content.toString());
    } catch (parseErr) {
        logger.error(
            { correlationId, err: parseErr },
            'Failed to parse infra.created event — discarding',
        );
        channel.nack(msg, false, false);
        return;
    }

    const { user_id, infra_id } = content.payload ?? {};

    logger.info({ correlationId, user_id, infra_id }, 'Received infra.created event');

    // Step 2: Validate required fields
    if (!user_id || !infra_id) {
        logger.warn(
            { correlationId, payload: content.payload },
            'Missing user_id or infra_id in infra.created event — discarding',
        );
        channel.nack(msg, false, false);
        return;
    }

    // Step 3: DB write with idempotency guard
    try {
        const user = await User.findByPk(user_id);
        if (user) {
            const currentInfraIds = user.infra_id || [];
            if (!currentInfraIds.includes(infra_id)) {
                logger.info(
                    { correlationId, user_id, infra_id },
                    'DB write: updating user infra_id array',
                );
                user.infra_id = [...currentInfraIds, infra_id];
                await user.save();
                logger.info(
                    { correlationId, user_id, infra_id, total_infra: user.infra_id.length },
                    'DB write: user infra_id updated successfully',
                );
            } else {
                logger.info(
                    { correlationId, user_id, infra_id },
                    'Idempotent skip: user already has infra_id',
                );
            }
        } else {
            logger.warn(
                { correlationId, user_id },
                'User not found during infra.created sync — nacking to dead-letter',
            );
            channel.nack(msg, false, false);
            return;
        }

        channel.ack(msg);
    } catch (err) {
        logger.error(
            { correlationId, user_id, infra_id, err },
            'DB write failed for infra.created — nacking to dead-letter',
        );
        channel.nack(msg, false, false);
    }
};

export const infraCreatedConsumer = new ResilientAmqpConsumer({
    url: env.RABBITMQ_URL,
    exchange: INFRA_EVENT_EXCHANGE,
    exchangeType: 'topic',
    queue: QUEUE_NAME,
    routingKey: INFRA_CREATED_ROUTING_KEY,
    prefetchCount: env.AMQP_PREFETCH_COUNT,
    maxRetries: env.AMQP_MAX_RETRIES,
    retryDelay: env.AMQP_RETRY_DELAY_MS,
    name: 'auth-infra-created',
});

export const startInfraConsumer = async () => {
    if (!env.RABBITMQ_URL) {
        logger.error('RABBITMQ_URL not set in auth service');
        return;
    }
    await infraCreatedConsumer.start(handler);
    logger.info('Auth service InfraCreatedConsumer started');
};

export const stopInfraConsumer = async () => {
    await infraCreatedConsumer.stop();
    logger.info('Auth service InfraCreatedConsumer stopped');
};

import { Queue } from 'bullmq';
import { redisConfig } from '@/client/redis';
import { NOTIFICATION_EVENT_QUEUE } from '@launchpad/common';
import {
    ResilientAmqpPublisher,
    AUTH_USER_REGISTERED_ROUTING_KEY,
    AUTH_EVENT_EXCHANGE,
    type AuthUserRegisteredPayload,
} from '@launchpad/common';
import { env } from '@/config/env';
import { logger } from '@/utils/logger';

export const userAuthenticationQueue = new Queue(NOTIFICATION_EVENT_QUEUE, {
    connection: redisConfig,
});

export const userCreatedPublisher = new ResilientAmqpPublisher({
    url: env.RABBITMQ_URL,
    exchange: AUTH_EVENT_EXCHANGE,
    exchangeType: 'topic',
    maxRetries: env.AMQP_MAX_RETRIES,
    retryDelay: env.AMQP_RETRY_DELAY_MS,
    maxBufferSize: 200,
    name: 'auth-user-created',
});

export const InitPublisher = async () => {
    if (!env.RABBITMQ_URL) {
        logger.error('RABBITMQ_URL not set in auth service');
        return;
    }
    await userCreatedPublisher.connect();
    logger.info('Auth service RabbitMQ publisher initialized');
};

export const PublishUserRegistered = (payload: AuthUserRegisteredPayload) => {
    const event = {
        type: AUTH_USER_REGISTERED_ROUTING_KEY,
        payload,
        occured_at: new Date().toISOString(),
        metadata: { version: 1 },
    };

    userCreatedPublisher.publish(
        AUTH_USER_REGISTERED_ROUTING_KEY,
        Buffer.from(JSON.stringify(event)),
        { contentType: 'application/json', persistent: true },
    );
};

export const closePublisher = async () => {
    await userCreatedPublisher.close();
    logger.info('Auth service RabbitMQ publisher closed');
};

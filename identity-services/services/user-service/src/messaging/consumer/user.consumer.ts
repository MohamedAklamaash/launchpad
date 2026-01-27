import { AUTH_EVENT_EXCHANGE, AUTH_USER_REGISTERED_ROUTING_KEY, INFRA_EVENT_EXCHANGE, INFRA_CREATED_ROUTING_KEY } from '@launchpad/common';
import amqplib, { ConsumeMessage } from 'amqplib';

import type { AuthRegisteredEvent, AuthUserRegisteredPayload, InfraCreatedEvent } from '@launchpad/common';
import type { Channel, ChannelModel, Connection } from 'amqplib';

import { env } from '@/config/env';
import { logger } from '@/utils/logger';
import { userService } from '@/service/user.service';

type ManagedConnection = Connection & Pick<ChannelModel, 'close' | 'createChannel'>;

let connection: ManagedConnection | null = null;
let channel: Channel | null = null;

const messagingEnabled = Boolean(env.RABBITMQ_URL);

const ensureChannel = async (): Promise<Channel | null> => {
    if (!messagingEnabled) {
        return null;
    }

    if (channel) {
        return channel;
    }

    if (!env.RABBITMQ_URL) {
        return null;
    }

    const amqpConnection = (await amqplib.connect(env.RABBITMQ_URL)) as unknown as ManagedConnection;
    connection = amqpConnection;
    amqpConnection.on('close', () => {
        logger.warn('RabbitMQ connection closed');
        connection = null;
        channel = null;
    });
    amqpConnection.on('error', (error) => {
        logger.error({ err: error }, 'RabbitMQ connection error');
    });

    const amqpChannel = await amqpConnection.createChannel();
    channel = amqpChannel;
    await amqpChannel.assertExchange(AUTH_EVENT_EXCHANGE, 'topic', { durable: true });
    await amqpChannel.assertExchange(INFRA_EVENT_EXCHANGE, 'topic', { durable: true });

    return amqpChannel;
};

export const initMessaging = async () => {
    if (!messagingEnabled) {
        logger.info('RabbitMQ URL is not configured; messaging disabled');
        return;
    }

    await ensureChannel();
    logger.info('User service RabbitMQ publisher initialized');

    if (channel) {
        const queue = await channel.assertQueue('user-service.auth-events', { durable: true });
        await channel.bindQueue(queue.queue, AUTH_EVENT_EXCHANGE, AUTH_USER_REGISTERED_ROUTING_KEY);

        await channel.consume(queue.queue, async (msg: ConsumeMessage | null) => {
            if (!msg) return;
            try {
                const event = JSON.parse(msg.content.toString()) as AuthRegisteredEvent;
                logger.info({ event }, 'Received auth user registered event');

                await userService.syncFromAuthUser(event.payload);

                channel?.ack(msg);
            } catch (error) {
                logger.error({ err: error }, 'Failed to process auth event');
                channel?.nack(msg, false, false); // Dead letter or discard if failure
            }
        });

        const infraQueue = await channel.assertQueue('user-service.infra-events', { durable: true });
        await channel.bindQueue(infraQueue.queue, INFRA_EVENT_EXCHANGE, INFRA_CREATED_ROUTING_KEY);

        await channel.consume(infraQueue.queue, async (msg: ConsumeMessage | null) => {
            if (!msg) return;
            try {
                const event = JSON.parse(msg.content.toString()) as InfraCreatedEvent;
                logger.info({ event }, 'Received infra created event');

                await userService.syncInfraCreation(event.payload);

                channel?.ack(msg);
            } catch (error) {
                logger.error({ err: error }, 'Failed to process infra event');
                channel?.ack(msg); // Ack to avoid loops if persistent failure
            }
        });

        logger.info('User service RabbitMQ consumers started');
    }
};

export const closeMessaging = async () => {
    try {
        if (channel) {
            const currentChannel: Channel = channel;
            channel = null;
            await currentChannel.close();
        }
        if (connection) {
            const currentConnection: ManagedConnection = connection;
            connection = null;
            await currentConnection.close();
        }

        logger.info('User service RabbitMQ publisher closed');
    } catch (error) {
        logger.error({ err: error }, 'Error closing RabbitMQ connection/channel');
    }
};

export const publishUserCreatedEvent = async (payload: AuthUserRegisteredPayload) => {
    const ch = await ensureChannel();

    if (!ch) {
        logger.debug({ payload }, 'Skipping user.created event publish; messaging disabled');
        return;
    }

    const event = {
        type: AUTH_USER_REGISTERED_ROUTING_KEY,
        payload,
        occurredAt: new Date().toISOString(),
        metadata: { version: 1 },
    };

    try {
        const sucess = ch.publish(
            AUTH_EVENT_EXCHANGE,
            AUTH_USER_REGISTERED_ROUTING_KEY,
            Buffer.from(JSON.stringify(event)),
            { contentType: 'application/json', persistent: true },
        );

        if (!sucess) {
            logger.warn({ event }, 'Failed to publish user.created event');
        }
    } catch (error) {
        logger.error({ err: error }, 'Error publishing user.created event');
    }
};
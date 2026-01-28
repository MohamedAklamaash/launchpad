import { Queue } from "bullmq";
import { redisConfig } from "@/client/redis";
import { NOTIFICATION_EVENT_QUEUE } from "@launchpad/common";

import { AUTH_USER_REGISTERED_ROUTING_KEY, AUTH_EVENT_EXCHANGE, type AuthUserRegisteredPayload } from "@launchpad/common"
import { connect, type Channel, type ChannelModel } from "amqplib";
import { env } from "@/config/env";
import { logger } from "@/utils/logger";

let channel: Channel | null = null
let connectionRef: ChannelModel | null = null

// this queue is used to send event to the notification service
export const userAuthenticationQueue = new Queue(NOTIFICATION_EVENT_QUEUE, {
    connection: redisConfig,
});

// this is for user created using rmq
export const InitPublisher = async () => {
    if (!env.RABBITMQ_URL) {
        logger.error('RABBITMQ_URL not set in auth service')
        return
    }
    if (channel) {
        return
    }
    const connection: ChannelModel = await connect(env.RABBITMQ_URL)
    connectionRef = connection
    channel = await connection.createChannel()

    await channel.assertExchange(AUTH_EVENT_EXCHANGE, "topic", {
        durable: true
    })

    connection.on("close", () => {
        logger.warn('RabbitMQ connection closed');
        channel = null;
        connectionRef = null;
    })

    connection.on('error', (err) => {
        logger.error({ err }, 'RabbitMQ connection error');
    });

    logger.info('Auth service RabbitMQ publisher initialized');
}

export const PublishUserRegistered = (payload: AuthUserRegisteredPayload) => {
    if (!channel) {
        logger.warn('RabbitMQ channel is not initialized. Cannot publish message.');
        return;
    }

    const event = {
        type: AUTH_USER_REGISTERED_ROUTING_KEY,
        payload,
        occured_at: new Date().toISOString(),
        metadata: { version: 1 },
    };

    const published = channel.publish(
        AUTH_EVENT_EXCHANGE,
        AUTH_USER_REGISTERED_ROUTING_KEY,
        Buffer.from(JSON.stringify(event)),
        { contentType: 'application/json', persistent: true }
    )

    if (!published) {
        logger.warn("Failed to publish user created event")
    }
}

export const closePublisher = async () => {
    try {
        const ch = channel;
        if (ch) {
            await ch.close();
            channel = null;
        }
        const conn = connectionRef;
        if (conn) {
            await conn.close();
            connectionRef = null;
        }
    } catch (error) {
        logger.error({ err: error }, 'Error closing RabbitMQ connection/channel');
    }
}

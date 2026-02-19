import {
    AUTH_EVENT_EXCHANGE,
    AUTH_USER_REGISTERED_ROUTING_KEY,
    INFRA_EVENT_EXCHANGE,
    INFRA_CREATED_ROUTING_KEY,
    ResilientAmqpConsumer,
    ResilientAmqpPublisher,
    type AuthRegisteredEvent,
    type AuthUserRegisteredPayload,
    type InfraCreatedEvent,
    type MessageHandler,
} from "@launchpad/common";
import { env } from "@/config/env";
import { logger } from "@/utils/logger";
import { userService } from "@/service/user.service";

// Publisher for user.created events (if needed by other services)
export const userCreatedPublisher = new ResilientAmqpPublisher({
    url: env.RABBITMQ_URL,
    exchange: AUTH_EVENT_EXCHANGE,
    exchangeType: "topic",
    maxRetries: env.AMQP_MAX_RETRIES,
    retryDelay: env.AMQP_RETRY_DELAY_MS,
    name: "user-service-publisher",
});

// Consumer for auth events (user registration)
const authHandler: MessageHandler = async (msg, channel) => {
    const correlationId =
        (msg.properties.correlationId as string | undefined) ?? crypto.randomUUID();
    try {
        const event = JSON.parse(msg.content.toString()) as AuthRegisteredEvent;
        logger.info(
            { correlationId, event_type: event.type, user_id: event.payload.id, metadata: event.payload.metadata },
            "Received auth.user.registered event",
        );
        await userService.syncFromAuthUser(event.payload);
        logger.info({ correlationId, user_id: event.payload.id }, "DB write: user synced");
        channel.ack(msg);
    } catch (error) {
        logger.error({ correlationId, err: error }, "Failed to process auth event — nacking to dead-letter");
        channel.nack(msg, false, false);
    }
};

export const authConsumer = new ResilientAmqpConsumer({
    url: env.RABBITMQ_URL,
    exchange: AUTH_EVENT_EXCHANGE,
    exchangeType: "topic",
    queue: "user-service.auth-events",
    routingKey: AUTH_USER_REGISTERED_ROUTING_KEY,
    prefetchCount: env.AMQP_PREFETCH_COUNT,
    maxRetries: env.AMQP_MAX_RETRIES,
    retryDelay: env.AMQP_RETRY_DELAY_MS,
    name: "user-auth-consumer",
});

// Consumer for infra events
const infraHandler: MessageHandler = async (msg, channel) => {
    const correlationId =
        (msg.properties.correlationId as string | undefined) ?? crypto.randomUUID();
    try {
        const event = JSON.parse(msg.content.toString()) as InfraCreatedEvent;
        logger.info(
            { correlationId, event_type: event.type, infra_id: event.payload?.infra_id },
            "Received infra.created event",
        );
        await userService.syncInfraCreation(event.payload);
        logger.info(
            { correlationId, infra_id: event.payload?.infra_id },
            "DB write: synced infra creation",
        );
        channel.ack(msg);
    } catch (error) {
        logger.error(
            { correlationId, err: error },
            "Failed to process infra event — nacking to dead-letter",
        );
        // FIXED: was channel.ack(msg) — that silently swallowed failures
        channel.nack(msg, false, false);
    }
};

export const infraConsumer = new ResilientAmqpConsumer({
    url: env.RABBITMQ_URL,
    exchange: INFRA_EVENT_EXCHANGE,
    exchangeType: "topic",
    queue: "user-service.infra-events",
    routingKey: INFRA_CREATED_ROUTING_KEY,
    prefetchCount: env.AMQP_PREFETCH_COUNT,
    maxRetries: env.AMQP_MAX_RETRIES,
    retryDelay: env.AMQP_RETRY_DELAY_MS,
    name: "user-infra-consumer",
});

export const initMessaging = async () => {
    if (!env.RABBITMQ_URL) {
        logger.info("RabbitMQ URL is not configured; messaging disabled");
        return;
    }

    await Promise.all([
        userCreatedPublisher.connect(),
        authConsumer.start(authHandler),
        infraConsumer.start(infraHandler),
    ]);

    logger.info("User service messaging components initialized");
};

export const closeMessaging = async () => {
    await Promise.all([
        userCreatedPublisher.close(),
        authConsumer.stop(),
        infraConsumer.stop(),
    ]);
    logger.info("User service messaging components closed");
};

export const publishUserCreatedEvent = async (payload: AuthUserRegisteredPayload) => {
    if (!env.RABBITMQ_URL) return;

    const event = {
        type: AUTH_USER_REGISTERED_ROUTING_KEY,
        payload,
        occured_at: new Date().toISOString(),
        metadata: { version: 1 },
    };

    userCreatedPublisher.publish(
        AUTH_USER_REGISTERED_ROUTING_KEY,
        Buffer.from(JSON.stringify(event))
    );
};
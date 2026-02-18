import {
    ResilientAmqpConsumer,
    INFRA_EVENT_EXCHANGE,
    INFRA_CREATED_ROUTING_KEY,
    type MessageHandler,
} from "@launchpad/common";
import { env } from "@/config/env";
import { logger } from "@/utils/logger";
import { User } from "@/db/models/user.model";

const QUEUE_NAME = "auth-service.infra-events";

const handler: MessageHandler = async (msg, channel) => {
    const content = JSON.parse(msg.content.toString());
    const { user_id, infra_id } = content.payload;


    const user = await User.findByPk(user_id);
    if (user) {
        const currentInfraIds = user.infra_id || [];
        if (!currentInfraIds.includes(infra_id)) {
            user.infra_id = [...currentInfraIds, infra_id];
            await user.save();
            logger.info({ user_id, infra_id }, "Updated user with new infra_id");
        } else {
            logger.info({ user_id, infra_id }, "User already has infra_id – skipping");
        }
    } else {
        logger.warn({ user_id }, "User not found during infra.created sync");
    }

    channel.ack(msg);
};

export const infraCreatedConsumer = new ResilientAmqpConsumer({
    url: env.RABBITMQ_URL,
    exchange: INFRA_EVENT_EXCHANGE,
    exchangeType: "topic",
    queue: QUEUE_NAME,
    routingKey: INFRA_CREATED_ROUTING_KEY,
    prefetchCount: env.AMQP_PREFETCH_COUNT,
    maxRetries: env.AMQP_MAX_RETRIES,
    retryDelay: env.AMQP_RETRY_DELAY_MS,
    name: "auth-infra-created",
});

export const startInfraConsumer = async () => {
    if (!env.RABBITMQ_URL) {
        logger.error("RABBITMQ_URL not set in auth service");
        return;
    }
    await infraCreatedConsumer.start(handler);
    logger.info("Auth service InfraCreatedConsumer started");
};

export const stopInfraConsumer = async () => {
    await infraCreatedConsumer.stop();
    logger.info("Auth service InfraCreatedConsumer stopped");
};

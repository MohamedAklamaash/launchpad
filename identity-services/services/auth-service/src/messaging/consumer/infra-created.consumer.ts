import { connect, Channel, ChannelModel } from "amqplib";
import { env } from "@/config/env";
import { logger } from "@/utils/logger";
import { User } from "@/db/models/user.model";
import { INFRA_EVENT_EXCHANGE, INFRA_CREATED_ROUTING_KEY } from "@launchpad/common";

export class InfraCreatedConsumer {
    private channel: Channel | null = null;
    private connection: ChannelModel | null = null;
    private QUEUE_NAME = "auth-service.infra-events";

    public async start() {
        try {
            if (!env.RABBITMQ_URL) {
                logger.error("RABBITMQ_URL not set in auth service");
                return;
            }

            this.connection = await connect(env.RABBITMQ_URL);
            this.channel = await this.connection.createChannel();

            await this.channel.assertExchange(INFRA_EVENT_EXCHANGE, "topic", { durable: true });
            await this.channel.assertQueue(this.QUEUE_NAME, { durable: true });
            await this.channel.bindQueue(this.QUEUE_NAME, INFRA_EVENT_EXCHANGE, INFRA_CREATED_ROUTING_KEY);

            this.channel.consume(this.QUEUE_NAME, async (msg) => {
                if (msg) {
                    try {
                        const content = JSON.parse(msg.content.toString());
                        const { user_id, infra_id } = content.payload;

                        logger.info(`Processing infra.created event for user ${user_id}, infra ${infra_id}`);

                        const user = await User.findByPk(user_id);
                        if (user) {
                            const currentInfraIds = user.infra_id || [];
                            if (!currentInfraIds.includes(infra_id)) {
                                user.infra_id = [...currentInfraIds, infra_id];
                                await user.save();
                                logger.info(`Updated user ${user_id} with new infra_id ${infra_id}`);
                            } else {
                                logger.info(`User ${user_id} already has infra_id ${infra_id}`);
                            }
                        } else {
                            logger.warn(`User ${user_id} not found during infra.created sync`);
                        }

                        this.channel?.ack(msg);
                    } catch (err) {
                        logger.error({ err }, "Error processing infra.created event in auth-service");
                        // Don't nack for now to avoid loops, or use dead letter exchange
                        this.channel?.ack(msg);
                    }
                }
            });

            logger.info("Auth service InfraCreatedConsumer initialized");
        } catch (err) {
            logger.error({ err }, "Failed to start InfraCreatedConsumer in auth-service");
        }
    }

    public async stop() {
        await this.channel?.close();
        await this.connection?.close();
    }
}

export const infraCreatedConsumer = new InfraCreatedConsumer();

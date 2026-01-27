import { createApp } from "./app";
import { env } from "@/config/env";
import { logger } from "@/utils/logger";
import { createServer } from "node:http";
import { closeDatabase, connectToDatabase } from "@/db/sequalize";
import { initModels } from "@/db";
import { closePublisher, InitPublisher } from "@/messaging/producer/user-created.message";
import { infraCreatedConsumer } from "@/messaging/consumer/infra-created.consumer";

const main = async () => {

    await connectToDatabase();
    await initModels();
    await InitPublisher();
    await infraCreatedConsumer.start();

    const app = createApp();
    const server = createServer(app);

    const shutdown = () => {
        Promise.all([closeDatabase(), closePublisher(), infraCreatedConsumer.stop()]).catch((error: unknown) => {
            logger.error({ error }, "Error shutting down tasks")
        }).finally(() => {
            server.close(() => {
                process.exit(0);
            });
            logger.info("Server closed!")
        })
    };

    process.on("SIGINT", shutdown);
    process.on("SIGTERM", shutdown);

    server.listen(env.AUTH_SERVICE_PORT, () => {
        logger.info(`Auth service running on port ${env.AUTH_SERVICE_PORT}`);
    })
}

void main()
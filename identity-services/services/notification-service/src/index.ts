import { createApp } from "./app";
import { env } from "@/config/env";
import { logger } from "@/utils/logger";
import { createServer } from "node:http";
import { ConnectMongoDB, CloseMongoConnection } from "@/db/client/mongo.client";

const main = async () => {

    await ConnectMongoDB()

    const app = createApp();
    const server = createServer(app);

    const shutdown = () => {
        Promise.all([CloseMongoConnection()]).catch((error: unknown) => {
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
    process.on("SIGQUIT", shutdown);

    server.listen(env.NOTIFICATION_SERVICE_PORT, () => {
        logger.info(`Notification service running on port ${env.NOTIFICATION_SERVICE_PORT}`);
    })

}

void main()
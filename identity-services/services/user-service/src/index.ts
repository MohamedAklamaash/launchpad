import { createApp } from "./app";
import { env } from "@/config/env";
import { logger } from "@/utils/logger";
import { createServer } from "node:http";
import { ConnectToDatabase, CloseDatabase } from "@/db/sequalize";
import { initMessaging, closeMessaging } from "@/messaging/consumer/user.consumer";
import { initModels } from "@/db";

const main = async () => {

    await ConnectToDatabase();
    await initModels()
    await initMessaging();

    const app = createApp();
    const server = createServer(app);

    const shutdown = () => {
        Promise.all([CloseDatabase(), closeMessaging()]).catch((error: unknown) => {
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

    server.listen(env.USER_SERVICE_PORT, () => {
        logger.info(`User service running on port ${env.USER_SERVICE_PORT}`);
    })

}

void main()
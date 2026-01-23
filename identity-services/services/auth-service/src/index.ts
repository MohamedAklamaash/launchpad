import { createApp } from "./app";
import { env } from "@/config/env";
import { logger } from "@/utils/logger";
import { createServer } from "node:http";
import { closeDatabase, connectToDatabase } from "./db/sequalize";
import { initModels } from "./db";

const main = async () => {

    await connectToDatabase();
    await initModels();

    const app = createApp();
    const server = createServer(app);

    const shutdown = () => {
        Promise.all([closeDatabase()]).catch((error: unknown) => {
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

    server.listen(env.AUTH_SERVICE_PORT, () => {
        logger.info(`Auth service running on port ${env.AUTH_SERVICE_PORT}`);
    })

}

void main()
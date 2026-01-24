import { CreateLogger, AppLogger } from "@launchpad/common";

export const logger: AppLogger = CreateLogger({
    name: "notification-service"
})
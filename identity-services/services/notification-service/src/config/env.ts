import "dotenv/config"
import { createEnv, z } from "@launchpad/common";

const envSchema = z.object({
    NODE_ENV: z.enum(["development", "test", "staging", "production"]),
    NOTIFICATION_SERVICE_PORT: z.coerce.number().default(3000),
})

export const env = createEnv(envSchema,
    {
        serviceName: "notification-service",
        source: process.env
    }
)

export type Env = typeof env;
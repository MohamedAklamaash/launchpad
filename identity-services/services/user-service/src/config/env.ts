import "dotenv/config"
import { createEnv, z } from "@launchpad/common";

const envSchema = z.object({
    NODE_ENV: z.enum(["development", "test", "staging", "production"]),
    USER_SERVICE_PORT: z.coerce.number().default(3000),
    INTERNAL_API_TOKEN: z.string(),

    USER_DB_URL: z.string(),
    RABBITMQ_URL: z.string()
})

export const env = createEnv(envSchema,
    {
        serviceName: "user-service",
        source: process.env
    }
)

export type Env = typeof env;
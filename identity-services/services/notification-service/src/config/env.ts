import "dotenv/config"
import { createEnv, z } from "@launchpad/common";

const envSchema = z.object({
    NODE_ENV: z.enum(["development", "test", "staging", "production"]),
    NOTIFICATION_SERVICE_PORT: z.coerce.number().default(3000),
    MAIL_HOST: z.string().default("smtp.gmail.com"),
    MAIL_PORT: z.coerce.number().default(587),
    MAIL_USER: z.string(),
    MAIL_APP_PASSWORD: z.string(),

    REDIS_HOST: z.string(),
    REDIS_PORT: z.coerce.number().default(6379),
    REDIS_PASSWORD: z.string(),
    REDIS_DB: z.coerce.number().default(0),
    REDIS_USERNAME: z.string(),

    MONGODB_URL: z.string(),


    FROM_MAIL: z.string(),

    AUTH_SERVICE_URL: z.string()
})

export const env = createEnv(envSchema,
    {
        serviceName: "notification-service",
        source: process.env
    }
)

export type Env = typeof env;
import "dotenv/config";
import { createEnv, z } from "@launchpad/common";

const envSchema = z.object({
    NODE_ENV: z.enum(["development", "test", "staging", "production"]),
    AUTH_SERVICE_PORT: z.coerce.number().default(3000),

    DATABASE_USER_NAME: z.string(),
    DATABASE_PASSWORD: z.string(),
    DATABASE_HOST: z.string(),
    DATABASE_PORT: z.coerce.number().default(5432),
    DATABASE_NAME: z.string(),
    DATABASE_SSL: z.coerce.boolean().default(false),
    DATABASE_SSL_REJECT_UNAUTHORIZED: z.coerce.boolean().default(false),

    AUTH_DB_URL: z.string().url(),
});

export const env = createEnv(envSchema, {
    serviceName: "auth-service",
    source: process.env,
});

export type Env = typeof env;